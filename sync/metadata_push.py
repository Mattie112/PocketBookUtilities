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


@dataclass(frozen=True)
class MetadataChange:
    device_book_id: int
    calibre_id: int
    display_title: str
    columns: dict          # device column -> new value
    old: dict              # device column -> old value

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
                           push_series=True, missing_authors_only=False):
    """Work out what would change in ``books_impl``.

    *calibre_metadata* maps a calibre id to a dict with ``title``,
    ``authors`` (display form), ``author_sort``, ``series`` and
    ``series_index``.
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
                if 'first_author_letter' in caps.cols.get('books_impl', ()):
                    columns['first_author_letter'] = first_letter(sort)
                    old['first_author_letter'] = row.first_author_letter

        if push_series and caps.has_series:
            series = meta.get('series') or None
            index = meta.get('series_index')
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

        if columns:
            changes.append(MetadataChange(
                device_book_id=link.device_book_id,
                calibre_id=link.calibre_id,
                display_title=link.title or meta.get('title') or '',
                columns=columns, old=old))

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
    debug('metadata push wrote %d row(s) in %d group(s)'
          % (written, len(groups)))
    return written
