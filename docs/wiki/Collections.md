# Collections

PocketBook calls them collections on screen and keeps them in a `bookshelfs`
table in its database. The plugin can create them, add and remove books, and
read membership back into calibre.

## Where the names come from

Set on the Collections tab. Four sources:

| Source | Behaviour |
|---|---|
| **A custom column** (default, `PB Collections`) | Read **and** written. A collection made on the reader comes back into this column. |
| **Calibre tags** | Read only. Every tag becomes a collection. |
| **Series name** | Read only. Each series becomes a collection. |
| **A template** | Read only. A calibre template, e.g. `{series}` or `{#genre}`. |

Only the column source is two-way. The plugin will not edit your tags or your
series to mirror something you did on the reader, so with any of the other
three, changes made on the device have nowhere to go and are not proposed.

**Preview for 10 books** on that tab shows what the current source produces
before anything is written.

## What gets written

Per book, the plugin compares the collections the book should be in against
the ones it is in, and writes only the difference:

* **Added** — a new membership row, or an existing soft-deleted one
  undeleted.
* **Removed** — the membership is flagged as deleted, never erased. Only
  happens with *Remove books from device collections that calibre does not
  list* on, and those rows are shown in red in the review.
* **New collection** — a new shelf, with a UUID filled in.

The collection name table has a uniqueness constraint that *fails* an insert
rather than ignoring it, so a name that exists but was deleted earlier has to
be undeleted instead of re-created. Not doing that is the usual reason
collections appear to vanish and never come back.

Each new shelf gets a UUID. Leaving it empty breaks PocketBook's own cloud
sync, which expects one.

## Reading collections back

With the column source, membership found on the device is written into the
mapped column, so a collection you create on the reader shows up in calibre
on the next sync, and the two stay in step from then on.

## Notes

* A device whose firmware has no shelves table has the collection menu
  entries greyed out, and the capability list on the Device tab says so.
* Nothing is ever deleted outright. If a collection looks wrong, the rows are
  still there, flagged, and a backup from before the write is on disk.
