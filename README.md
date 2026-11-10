# PocketBook Utilities

[![CI](https://github.com/Mattie112/PocketBookUtilities/actions/workflows/ci.yml/badge.svg)](https://github.com/Mattie112/PocketBookUtilities/actions/workflows/ci.yml)

A calibre plugin for PocketBook e-readers. It syncs reading status, reading
position, progress, favourites and collections between the reader and your
calibre library, and can fix the author information PocketBook sorts by.

Think of it as the PocketBook equivalent of
[Kobo Utilities](https://github.com/janlarres/kobo-utilities).

> **Written with AI assistance.** Most of this code, and this README, were
> produced by Claude (Anthropic) working from my direction, and the commits
> carry a `Co-Authored-By` trailer saying so. It isn't unreviewed output: it
> was built against a real InkPad 3 Color, every design decision was checked
> against the device's actual database, and it ships with a test suite that
> runs on calibre's own Python. Several bugs listed in the history were found
> by using it rather than by reading it. Treat it as you would any new
> plugin that touches your library: it takes a backup before every write,
> but read the Safety section and keep your own backups.

## What it syncs

| | Device field | calibre column | Direction |
|---|---|---|---|
| Read status | `books_settings.completed` | `#pb_read` | both ways |
| Read date | `completed_ts` | `#pb_read_date` | device to calibre |
| Last opened | `opentime` | `#pb_last_read` | device to calibre |
| Favourites | `favorite` | `#pb_favourite` | both ways |
| Progress | `cpage` / `npage` | `#pb_progress` | device to calibre |
| Reading position | `position` | `#pb_position` | both ways |
| Collections | `bookshelfs` | `#pb_collections` | both ways |

Positions are stored by the reader as a `pbr:` URI around an EPUB CFI, like
`pbr:/webkit?##epubcfi(/6/62!/4/2/4/1:1)`. Progress comes from the reader's
own page counters, so a book sitting at page 144 of 387 of *Dungeon Crawler
Carl* shows up as 37%.

There are a few extras:

* **Push titles, authors and series to the device.** PocketBook fills in
  the fields it sorts and groups by from the filename, which calibre
  generated from the book's metadata when it sent the file. This rewrites
  them from what calibre holds now.

  Worth being clear about what that does and does not fix. If the reader
  files a book oddly, the cause is usually the metadata in calibre, and
  this will faithfully push the same oddity back. What it is actually for
  is the case where the two have diverged: metadata corrected in calibre
  after the book was sent, or a book that reached the device some other
  way. It saves deleting and re-sending the file.
* Automatic backups before every write, and a restore dialog.
* A database integrity check that reports orphan rows without touching them.
* A list of books on the device that aren't in your library.

## Compatibility

Tested on a PocketBook InkPad 3 Color, library schema version 39. Every
feature has been run against it: reading status, dates, progress and
positions synced both ways across a 473-book library; collections created,
shared between books, removed and re-added; authors pushed to the device,
after which the reader's own trigger rewrote its full-text search index as
expected. Writes were confirmed to survive disconnecting the reader.

It should work on any PocketBook whose firmware keeps its library in
`system/explorer-3/explorer-3.db`, which covers firmware 5.x and 6.x and so
most current models. `explorer-2.db` is tried as a fallback for older
firmware but hasn't been tested.

Devices aren't recognised by model or USB id. The plugin looks for the
database file under the connected device's storage, so it doesn't matter
which driver calibre picks. (The test device is matched by calibre as "Diva
HD", which it handles fine.)

Capabilities are read from the database at connect time with
`PRAGMA table_info`. If a firmware is missing a column or table, the feature
that needs it is disabled with a reason shown in the UI rather than failing
at runtime. **Show device information** lists what your reader supports.

Not tested on real hardware, though covered by fixtures:

* SD card storage
* multiple user profiles
* `explorer-2.db` firmware

CoolReader positions (`.cr3hist`) are detected but not implemented. The test
device reads EPUBs with the native engine, which keeps position in the
database.

If you run this on a different PocketBook, please open an issue with the
output of **Show device information**. It contains the model, schema version
and capability flags, and no titles or file paths.

## Requirements

calibre 5.35 or newer (that's when programmatic column creation was added);
developed against 9.8. The reader needs to be connected in USB mass storage
mode rather than MTP. There are no Python dependencies.

## Installing

In calibre: Preferences, Plugins, *Load plugin from file*, pick
`PocketBook Utilities.zip`, restart.

Or from a terminal:

```bash
calibre-customize -a "PocketBook Utilities.zip"
```

Installing this way registers the plugin but won't add a toolbar button,
because calibre's default toolbar only contains built-in actions. The plugin
notices and offers to add one on startup.

## First run

Connect the reader and a setup dialog appears. Press **Set up columns** to
create the custom columns, then restart calibre so they load. (Syncing is
blocked until you do, with a message saying which columns it's waiting for.)
Then use **Sync now (review changes)**.

Nothing is written until you've seen the list of proposed changes and pressed
*Apply selected*.

## How conflicts are resolved

The plugin records what each side looked like after the last successful sync,
so it can tell which side actually changed:

| Device changed | calibre changed | Result |
|---|---|---|
| no | no | nothing proposed |
| yes | no | load into calibre |
| no | yes | send to device |
| yes | yes | newer wins, flagged in the review list |

A sync with nothing to do proposes nothing, so repeated runs don't rewrite
your library or bump `last_modified` on every book.

**Send to device** and **Load from device** skip the merge when you want one
side to win outright.

Each proposed change shows its direction and why it was chosen. Books matched
by title and author rather than by file path are listed unticked, since those
matches are less certain.

## Collections

Collection names can come from a custom column (`#pb_collections` by
default), calibre tags, the series name, or a template. The config dialog has
a preview button to see what a given source produces.

## Safety

The plugin contains no `DELETE` or `DROP` statement; a unit test checks this.
Removing a book from a collection sets `is_deleted`, which is what the
firmware does itself.

Backups go to `<calibre config>/plugins/pocketbook_utilities/backups/<serial>/`
before every write. The newest ten are kept along with the first one ever
taken.

Reads don't modify the device. The database runs in WAL mode, so a plain file
copy can return stale data; the plugin takes a snapshot through SQLite's
online backup API and works from that. Writes happen in a single short
transaction on the device.

All SQL is parameterised, so apostrophes in titles, authors and collection
names are just data.

### Ejecting

PocketBook can rewrite its library database from memory when the USB
connection drops, undoing changes made while it was connected. Eject the
reader from calibre before unplugging it.

The plugin records a receipt after each write and checks on the next
connection whether the changes survived, reporting it if they didn't. There's
also an *Eject automatically after writing* option on the Device tab.

## Troubleshooting

**No toolbar button.** calibre's default toolbar only holds built-in actions,
and resetting toolbars to defaults removes any plugin button. The plugin
offers to add one on startup, or add it under Preferences, Toolbars & menus,
to both *The main toolbar* and *The main toolbar when a device is connected*.

**"calibre has not loaded the new columns yet".** calibre only picks up new
columns on restart. Restart and sync again.

**Nothing proposed.** Normal when both sides agree. The summary line shows
how many books were matched.

**Books not matched.** Check *Books on the device not in this library*. Books
calibre didn't put there are never modified. If calibre thinks a book is on
the device but the reader's database doesn't list it, the reader hasn't
rescanned yet.

**Changes vanished after unplugging.** See the note on ejecting. *Verify the
last write* will confirm it.

## Implementation notes

All reading state lives in one table:

```sql
books_settings(bookid, profileid, position TEXT, position_ts INTEGER,
               cpage INTEGER, npage INTEGER, opentime INTEGER,
               completed INTEGER, completed_ts INTEGER,
               favorite INTEGER, favorite_ts INTEGER, custom TEXT,
               PRIMARY KEY(bookid, profileid))
```

Books are matched by resolving each storage root against the filesystem, then
by exact relative path, falling back to filename plus size, filename alone,
and finally title and author. Each match carries a confidence level.

Three firmware details are worth knowing before changing anything:

* `completed_ts` isn't yours to set. Triggers overwrite it with the current
  time on any update of `completed`, and on any insert.
* The full-text search index updates itself when `title` or `author` change,
  so fixing metadata keeps on-device search working.
* `bookshelfs.name` is `UNIQUE ... ON CONFLICT FAIL`, so a soft-deleted shelf
  has to be undeleted rather than re-inserted.

## Development

`device/` and `sync/` avoid Qt and the calibre GUI on purpose, so most of
the suite runs under any Python. The tests that do need calibre skip
themselves when it is absent.

```bash
python tests/run_tests.py               # 128 tests, no calibre needed
calibre-debug -e tests/run_tests.py     # all 179, including the dialogs
python build.py                         # build the zip
python build.py --version               # the version the zip will report
```

CI runs both: the first on plain Python across 3.10 to 3.13, the second on
a runner with calibre installed, under `xvfb`.

### Cutting a release

Merge what you want, then on GitHub: **Releases**, *Draft a new release*,
create a tag such as `v0.0.3` or `v0.0.3-alpha`, Publish. That is all of it.
The workflow stamps the plugin with the version from the tag, builds the
zip and attaches it to the release.

A tag ending in a suffix (`-alpha`, `-beta`, `-rc1`) is marked as a
pre-release. calibre plugin versions are tuples of integers, so the suffix
exists only in the tag; `v0.0.3-alpha` installs as version `0.0.3`.

`PLUGIN_VERSION` in `__init__.py` is the development default, used when you
build from a checkout. A release is stamped from its tag, so nothing needs
editing before tagging. To bump the default anyway:

```bash
python build.py --set-version 0.0.3
```

```
device/     detection, schema introspection, database access, backups
sync/       book matching, merge engine, collections, metadata push
common/     dialogs and shared helpers
action.py   toolbar menu and dispatch
runner.py   read device, build plan, apply
columns.py  custom column definitions and creation
```

`tests/schema_v39.sql` is dumped from a real device, triggers and indexes
included, and the fixtures are built from it. One test compares the fixture's
schema fingerprint against a connected device so firmware drift shows up as a
test failure. Others check that no destructive SQL creeps in, that SQL isn't
built by string concatenation, that exceptions aren't swallowed, and that a
second sync run proposes nothing.

## Licence

GPL v3, same as calibre. See `LICENSE`.

The architecture follows
[Kobo Utilities](https://github.com/janlarres/kobo-utilities). Initial
knowledge of the PocketBook schema came from
[Pocketbook Collections](https://github.com/Anareaty/pocketbook-collections)
by Anareaty.
