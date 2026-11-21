# PocketBook Utilities

A calibre plugin that keeps reading status, reading position, progress,
favourites and collections in step between a PocketBook e-reader and a calibre
library, and can repair the author information the reader sorts by.

<!-- screenshot: menu.png -- the plugin's drop-down menu, device connected -->
<!-- screenshot: review.png -- the review dialog with a few changes listed -->

## Start here

* [Installation and first run](Installation-and-first-run) — installing the
  plugin, getting a button on the toolbar, and the one-click column setup.
* [How syncing works](How-syncing-works) — what the plugin compares, how it
  decides which side wins, and what the review dialog is telling you.
* [Settings reference](Settings-reference) — every option in the
  configuration dialog, what it defaults to and what it changes.

## What it reads and writes

| | Direction |
|---|---|
| Read status | both ways |
| Read date | reader → calibre |
| Last opened | reader → calibre |
| Favourite | both ways |
| Progress (% read) | reader → calibre |
| Reading position | both ways |
| Collections | both ways |

Reader-only fields are ones the device maintains itself; writing them back
would be meaningless or would be overwritten on the next page turn. The
[Columns](Columns) page says which calibre column each one lands in.

## Before you trust it with your library

The plugin writes directly to the reader's `explorer-3.db` database. It takes
a backup before every single write and that cannot be turned off, but read
[Backups and safety](Backups-and-safety) once before the first write, and keep
your own backups of your calibre library as you would with any plugin.

No `DELETE` statement exists anywhere in the plugin. Removing a book from a
collection flags the membership as deleted, the way the firmware does.

## Known problems

* Reading positions stored by **CoolReader** (`.cr3hist`) are detected but not
  synced. The native reader keeps its position in the database and works.
* The **Fix titles, authors and series** action always covers the whole
  library; it has no "selected books only" mode yet.
* SD-card storage, multiple device profiles and `explorer-2` firmware are
  implemented but have not been tested on real hardware. Reports welcome.

## Getting help

* [Issue tracker](https://github.com/Mattie112/PocketBookUtilities/issues)
* [MobileRead thread](https://www.mobileread.com/forums/showthread.php?t=376388)

If you are reporting a problem, [Troubleshooting](Troubleshooting) says which
three things to include.
