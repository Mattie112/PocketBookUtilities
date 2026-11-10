"""Push calibre metadata into ``books_impl``.

``firstauthor`` is the column the device sorts and groups its author index
by. The firmware fills it in from the filename, which calibre generated
from the book's metadata when it sent the file, so the two normally agree.

Which means this is **not** a fix for a reader that files books oddly. On
the device this was written against, a dozen books sat under "0"::

    author      = 'Clancy, Tom - Net Force Explorers 01'
    firstauthor = '01, Clancy, Tom - Net Force Explorers'

and calibre held exactly the same strings: the series had been folded into
the author name in the library itself. Pushing would have rewritten those
values with themselves. The repair belonged in calibre.

What this is for is the case where the two sides have genuinely diverged:
metadata corrected in calibre after the book was sent, or a book that
arrived on the device some other way. It propagates the current values
without deleting and re-sending the file.

``books_impl`` has real ``series`` and ``numinseries`` columns, so series
information goes there instead of being smuggled into the title.

One device-side detail worth knowing: a trigger keeps the full-text ``search``
table in step whenever ``title`` or ``author`` changes, so on-device search
stays correct without us touching the index.
"""

from dataclasses import dataclass
from typing import Optional

from ..common.compat import debug

#: Fields the user can push, in the order shown in the preview.
PUSH_FIELDS = ('title', 'author', 'firstauthor', 'series')

#: Columns computed from another one -> the column they follow. Writing one
#: half of such a pair leaves the device row contradicting itself, so a
#: derived column is never chosen separately from its source.
DERIVED_FROM = {'first_author_letter': 'firstauthor',
                'sort_title': 'title'}


@dataclass(frozen=True)
class MetadataChange:
    device_book_id: int
    calibre_id: int
    display_title: str
    columns: dict          # device column -> new value
    old: dict              # device column -> old value
    #: How the book was matched to the device, for the review list.
    match: str = ''
    #: False when that match is below what the user trusts. Such a change
    #: is still listed, but starts unticked: overwriting a title and author
    #: on the strength of a shared filename is how the wrong book gets them.
    trusted: bool = True

    @property
    def is_empty(self):
        return not self.columns


def first_letter(value):
    text = (value or '').strip()
    if not text:
        return None
    return text[0].upper()


def build_metadata_changes(links, device_rows, calibre_metadata, caps,
                           push_title=False, push_author=True,
                           push_series=True, missing_authors_only=False,
                           trust_floor=None):
    """Work out what would change in ``books_impl``.

    *calibre_metadata* maps a calibre id to a dict with ``title``,
    ``authors`` (display form), ``author_sort``, ``series`` and
    ``series_index``. Links matched less surely than *trust_floor* produce
    changes marked untrusted, exactly as the sync pre-unticks them.
    """
    changes = []
    for link in links:
        row = device_rows.get(link.device_book_id)
        meta = calibre_metadata.get(link.calibre_id)
        if row is None or meta is None:
            continue

        if missing_authors_only:
            current = (row.author or '').strip()
            if current and current.lower() != 'unknown':
                continue

        columns = {}
        old = {}

        if push_title and meta.get('title'):
            if (row.title or '') != meta['title']:
                columns['title'] = meta['title']
                old['title'] = row.title
                if caps.has_sort_title:
                    columns['sort_title'] = meta.get('title_sort') \
                        or meta['title']
                    old['sort_title'] = row.sort_title

        if push_author:
            display = meta.get('authors') or ''
            sort = meta.get('author_sort') or display
            if display and (row.author or '') != display:
                columns['author'] = display
                old['author'] = row.author
            if sort and (row.firstauthor or '') != sort:
                columns['firstauthor'] = sort
                old['firstauthor'] = row.firstauthor
            # The index letter follows the author sort, whether or not the
            # sort itself is moving: a book can hold the right name under
            # the wrong letter, and that is the half the reader files by.
            # Case is the firmware's business, so only a different letter
            # counts, not a differently cased one.
            letter = first_letter(sort)
            if (letter and 'first_author_letter'
                    in caps.cols.get('books_impl', ())
                    and letter.casefold()
                    != (row.first_author_letter or '').casefold()):
                columns['first_author_letter'] = letter
                old['first_author_letter'] = row.first_author_letter

        if push_series and caps.has_series:
            series = meta.get('series') or None
            # calibre keeps a series index of 1.0 on every book, series or
            # not. Without a series it means nothing, and pushing it would
            # number every unseried book on the reader as "1".
            index = meta.get('series_index') if series else None
            try:
                index = int(index) if index is not None else None
            except (TypeError, ValueError):
                index = None
            if (row.series or None) != series:
                columns['series'] = series
                old['series'] = row.series
            if (row.numinseries or None) != index:
                columns['numinseries'] = index
                old['numinseries'] = row.numinseries

        # Drop anything that would write back the value already there.
        # first_author_letter in particular is derived from firstauthor, so
        # it rides along unchanged whenever only the rest of the name moves,
        # and listing 'M' -> 'M' in the preview is pure noise.
        columns = {name: value for name, value in columns.items()
                   if value != old.get(name)}

        if columns:
            changes.append(MetadataChange(
                device_book_id=link.device_book_id,
                calibre_id=link.calibre_id,
                display_title=link.title or meta.get('title') or '',
                columns=columns,
                old={k: v for k, v in old.items() if k in columns},
                match=link.confidence.label,
                trusted=(trust_floor is None
                         or link.confidence >= trust_floor)))

    debug('metadata push: %d book(s) would change' % len(changes))
    return changes


ALLOWED_COLUMNS = frozenset({
    'title', 'sort_title', 'author', 'firstauthor', 'first_author_letter',
    'series', 'numinseries',
})


def update_statement(columns):
    """Parameterised UPDATE for ``books_impl``.

    Column names are checked against an allowlist, so nothing derived from
    book data can ever reach the statement text.
    """
    names = sorted(columns)
    unknown = set(names) - ALLOWED_COLUMNS
    if unknown:
        raise ValueError('refusing to update unknown columns: %r'
                         % sorted(unknown))
    assignments = ', '.join('%s = ?' % name for name in names)
    return 'UPDATE books_impl SET %s WHERE id = ?' % assignments, names


def apply_metadata(conn, changes):
    """Apply metadata changes. Caller owns the transaction."""
    groups = {}
    for change in changes:
        key = tuple(sorted(change.columns))
        groups.setdefault(key, []).append(change)

    written = 0
    for key, group in groups.items():
        statement, names = update_statement(key)
        rows = [tuple(c.columns[n] for n in names) + (c.device_book_id,)
                for c in group]
        conn.executemany(statement, rows)
        written += len(rows)
    reindexed = _reindex_search(conn, changes)
    debug('metadata push wrote %d row(s) in %d group(s), re-indexed %d'
          % (written, len(groups), reindexed))
    return written


SEARCH_COLUMNS = frozenset({'title', 'author'})

REINDEX_SQL = (
    'UPDATE search SET '
    'title = (SELECT title FROM books_impl WHERE id = ?), '
    'author = (SELECT author FROM books_impl WHERE id = ?) '
    'WHERE book_id = ?')


def _reindex_search(conn, changes):
    """Bring the device's full-text index in line with what was written.

    The firmware has a trigger for this, but it compares old and new with
    ``!=``, which is never true against NULL. A book whose author was
    missing -- the very case "only books with no author" exists for --
    would keep its empty search entry and stay unfindable by the name just
    given to it. Doing it here as well costs one statement per book and is
    harmless where the trigger did fire.
    """
    ids = [c.device_book_id for c in changes
           if SEARCH_COLUMNS & set(c.columns)]
    if not ids:
        return 0
    has_search = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'search'").fetchone()
    if has_search is None:
        return 0
    conn.executemany(REINDEX_SQL, [(i, i, i) for i in ids])
    return len(ids)
