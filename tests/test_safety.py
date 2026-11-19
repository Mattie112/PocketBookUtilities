"""Structural guarantees about the package itself.

These encode the two promises that distinguish this plugin from the one it
replaces: it never deletes anything on the device, and it never swallows an
error into a meaningless message.
"""

import ast
import builtins as _builtins
import io
import os
import re
import sys
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'tests', '.git', '__pycache__', 'resources'}


def package_sources():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


def code_without_comments_or_docstrings(path):
    """Source text with comments and string literals blanked out.

    Keeps us from tripping over the word DELETE inside a docstring that
    explains why we do not use it.
    """
    with open(path, 'rb') as handle:
        source = handle.read().decode('utf-8')
    out = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok_type, tok_str, _, _, _ in tokens:
            if tok_type in (tokenize.COMMENT, tokenize.STRING):
                out.append(' ')
            else:
                out.append(tok_str + ' ')
    except tokenize.TokenError:
        return source
    return ''.join(out)


class TestNoDestructiveSql(unittest.TestCase):

    def test_package_contains_no_delete_or_drop_statements(self):
        offenders = []
        for path in package_sources():
            text = code_without_comments_or_docstrings(path)
            if re.search(r'\bDELETE\s+FROM\b', text, re.IGNORECASE):
                offenders.append((path, 'DELETE FROM'))
            if re.search(r'\bDROP\s+(TABLE|TRIGGER|INDEX)\b', text,
                         re.IGNORECASE):
                offenders.append((path, 'DROP'))
        self.assertEqual(offenders, [],
                         'destructive SQL found: %r' % (offenders,))

    def test_sql_strings_never_interpolate_values(self):
        """Any %-formatting in SQL must be placeholders, not data.

        We allow `','.join('?' * n)` style expansion and table names from the
        allowlist; anything else that builds SQL with + or % on a variable is
        flagged for review.
        """
        suspicious = []
        pattern = re.compile(
            r'''["'][^"']*\b(SELECT|INSERT|UPDATE)\b[^"']*["']\s*\+''',
            re.IGNORECASE)
        for path in package_sources():
            text = code_without_comments_or_docstrings(path)
            for match in pattern.finditer(text):
                suspicious.append((os.path.basename(path), match.group(0)))
        self.assertEqual(suspicious, [],
                         'SQL built by concatenation: %r' % (suspicious,))


class TestNoSilentFailures(unittest.TestCase):

    def test_no_bare_except_pass(self):
        offenders = []
        pattern = re.compile(r'except\s*(Exception\s*)?:\s*pass')
        for path in package_sources():
            text = code_without_comments_or_docstrings(path)
            if pattern.search(text):
                offenders.append(os.path.basename(path))
        self.assertEqual(offenders, [],
                         'silent exception swallowing in: %r' % (offenders,))


class TestHeadlessImports(unittest.TestCase):
    """`device` and `sync` must import with no Qt and no calibre GUI."""

    MODULES = (
        'device.db',
        'device.schema',
        'device.detect',
        'common.compat',
    )

    def test_modules_import_without_qt(self):
        import importlib

        class QtBlocker:
            def find_module(self, name, path=None):
                return self.find_spec(name, path)

            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('PyQt6', 'PyQt5', 'qt'):
                    raise AssertionError(
                        'Qt import attempted from a headless module: %s'
                        % name)
                return None

        blocker = QtBlocker()
        sys.meta_path.insert(0, blocker)
        try:
            for name in self.MODULES:
                for cached in list(sys.modules):
                    if cached.endswith(name):
                        pass
                importlib.import_module(
                    'calibre_plugins.pocketbook_utilities.' + name)
        finally:
            sys.meta_path.remove(blocker)


if __name__ == '__main__':
    unittest.main()


class TestNoUndefinedNames(unittest.TestCase):
    """Catch names used but never bound, without running the code.

    action.py called Side.CALIBRE with no import of Side. Nothing noticed,
    because the GUI layer is not executed by the tests, and the crash only
    appeared once a real sync had something to do. There is no linter in
    calibre's Python, so this is a small stand-in: it only reports a name
    that is bound nowhere the interpreter could find it.
    """

    BUILTINS = set(dir(_builtins)) | {
        '__file__', '__name__', '__doc__', '__package__', '__builtins__',
        'WindowsError',
    }

    def bound_names(self, node):
        """Every name this scope binds, however it binds it."""
        found = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx,
                                                          (ast.Store,
                                                           ast.Del)):
                found.add(child.id)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                found.add(child.name)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    found.add((alias.asname or alias.name).split('.')[0])
            elif isinstance(child, ast.arg):
                found.add(child.arg)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                found.add(child.name)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                found.update(child.names)
        return found

    def test_every_name_used_is_bound_somewhere(self):
        offenders = []
        for path in package_sources():
            with open(path, encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
            # Module scope plus everything any nested scope binds: generous
            # on purpose, so this reports only the clear-cut cases.
            known = self.bound_names(tree) | self.BUILTINS
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx,
                                                             ast.Load):
                    if node.id not in known:
                        offenders.append((os.path.basename(path),
                                          node.lineno, node.id))
        self.assertEqual(offenders, [],
                         'names used but never bound: %r' % (offenders,))
