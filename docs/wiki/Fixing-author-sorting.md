# Fixing author sorting

**Send to device → Fix titles, authors and series on the device…**

This is not part of a sync. It is a one-way repair, it never runs
automatically, and it has its own review dialog.

## The problem

PocketBook sorts its author list by a field it fills in itself, often derived
from the folder or file name. When books were put on the device with the
series folded into the author, that field ends up as something like:

    01, Clancy, Tom - Net Force Explorers

which sorts under a digit, so the book lands in a `0` section of the author
list instead of under C.

## What it writes

For the books you tick, into the device's book table:

| Setting | Writes |
|---|---|
| Author and author sort | The author, and calibre's author sort into the field the device sorts by |
| Series name and number | The device's own series and number fields |
| Title | Title and sort title |
| Only books whose author is missing on the device | Limits the run to books with no author at all |

Defaults: author and series on, title off.

Because the device has real series fields, series information goes there
rather than being smuggled into the title.

## Check calibre first

The device usually got the bad value from the file it was given, which means
calibre has the same bad value. If calibre's own author sort reads
`Clancy| Tom - Net Force Explorers 01`, this action will faithfully send that
over and change nothing.

Fix the metadata in calibre first, then push it. The review dialog shows the
old and new value per book, so a run that proposes nothing useful is telling
you the problem is upstream.

## On-device search

The firmware keeps a full-text search index in step with title and author
changes by way of a trigger. That trigger compares old and new values with
`!=`, which is never true when the old value is NULL, so books that had no
author were silently missing from device search afterwards. The plugin
re-indexes those rows itself.

## Scope

The action covers the whole library; it has no selected-books-only mode yet.
The review dialog is per book, so you can still untick everything you do not
want.
