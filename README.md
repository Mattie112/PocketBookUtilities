# PocketBook Utilities

A [calibre](https://calibre-ebook.com/) plugin that keeps your **reading
status, reading position, progress, favourites and collections** in step
between a PocketBook e-reader and your calibre library — and repairs the
author information the reader sorts by.

It is what [Kobo
Utilities](https://github.com/janlarres/kobo-utilities) is for Kobo owners,
built for PocketBook.

---

## What it syncs

| | Device | calibre column | Direction |
|---|---|---|---|
| Read status | `books_settings.completed` | `#pb_read` | both ways |
| Read date | `completed_ts` | `#pb_read_date` | device → calibre |
| Last opened | `opentime` | `#pb_last_read` | device → calibre |
| Favourites | `favorite` | `#pb_favourite` | both ways |
| Progress | `cpage` / `npage` | `#pb_progress` | device → calibre |
| Reading position | `position` | `#pb_position` | both ways |
| Collections | `bookshelfs` + `bookshelfs_books` | `#pb_collections` | both ways |

Reading positions are stored by the reader as a `pbr:` URI wrapping a
standard EPUB CFI, for example:

```
pbr:/webkit?##epubcfi(/6/62!/4/2/4/1:1)
```

### Also included

- **Fix titles, authors and series on the device** — PocketBook fills in the
  field it sorts by from the filename, which often produces nonsense like
  `firstauthor = "01, Clancy, Tom - Net Force Explorers"` filed under `0`.
  This writes calibre's author sort, series and series number into the
  device database instead.
- **Automatic backups** before every write, with restore.
- **Database integrity check** — reports orphan rows, never removes them.
- **Books on the device not in this library** — a read-only report.
- **Verify the last write** — see *Why ejecting matters*, below.

---

## Device compatibility

**Verified on:** PocketBook InkPad 3 Color (`PB743K3`), library database
schema version 39.

**Expected to work on** any PocketBook whose firmware stores its library in
`system/explorer-3/explorer-3.db` — which is PocketBook firmware 5.x and
6.x, so most current models (Touch Lux, Touch HD, InkPad, InkPad X, InkPad
Color, Era, Verse, Basic Lux and relatives). `explorer-2.db` is probed as a
fallback for older firmware but has not been tested.

The plugin does **not** recognise devices by model, USB id or calibre driver.
It looks for `system/explorer-N/explorer-N.db` under the connected device's
storage, so it works whichever driver calibre happens to pick — useful,
because a PocketBook can be driven by calibre's built-in `POCKETBOOK*`
drivers, by the third-party *PocketBook Improved* driver, or be matched as
something else entirely. The author's device is matched by calibre as
"Diva HD" and the plugin is unbothered.

**Features are decided at runtime, not assumed.** On connecting, the plugin
reads the database schema with `PRAGMA table_info` and derives capability
flags. A firmware that lacks a column or a table loses *that one feature*,
disabled with a visible reason, rather than producing an error or writing
something wrong. **Show device information** lists exactly what your reader
supports.

### Known untested areas

These are implemented and unit-tested against fixtures, but no real hardware
has exercised them:

- SD-card storage (the reference device has none)
- Multiple user profiles (`system/profiles/.current.lnk`)
- `explorer-2.db` firmware
- CoolReader reading positions (`.cr3hist`) — **not implemented**. The
  reference device reads EPUBs with the native engine, which stores position
  in the database. Detection is in place, so the channel can be added.

**If you run this on another PocketBook**, please open an issue with the
output of *Show device information*. It contains model, schema version and
capability flags, and no book titles or file paths.

---

## Requirements

- calibre **5.35 or newer** (that is when programmatic custom-column creation
  arrived). Developed against calibre 9.8.
- The reader connected in **USB mass storage** mode, not MTP.
- Nothing else. No Python packages to install.

---

## Installing

**From calibre:** Preferences → Plugins → *Load plugin from file* → pick
`PocketBook Utilities.zip`. Restart calibre.

**From a terminal:**

```bash
calibre-customize -a "PocketBook Utilities.zip"
```

> **Note:** installing from the command line registers the plugin but does
> **not** put a button on the toolbar — calibre's default toolbar contains
> built-in actions only. The plugin notices this on startup and offers to add
> one. See *No toolbar button* under Troubleshooting.

---

## First run

1. Connect the reader. A setup dialog appears.
2. Press **Set up columns**. Every custom column the plugin needs is created
   in one go, with the right types.
3. **Restart calibre** so the new columns load. The plugin refuses to sync
   until you do, and says which columns it is waiting for.
4. **Sync now (review changes)**.

Nothing is written anywhere until you have seen the list of proposed changes
and pressed *Apply selected*.

---

## How it decides which side wins

The plugin remembers what each side looked like after the last successful
sync, in its own small database. That makes the merge a genuine three-way
merge rather than a timestamp comparison:

| Device changed? | calibre changed? | Result |
|---|---|---|
| no | no | nothing is proposed |
| yes | no | loaded into calibre |
| no | yes | sent to the device |
| yes | yes | the more recent wins, flagged in the review list |

An idle sync therefore proposes **nothing at all** — it does not rewrite your
library and churn `last_modified` on every book.

**Send to device** and **Load from device** override the merge when you want
one side to win outright.

Every proposed change is listed with its direction and the reason it was
chosen, sorted by book. Matches the plugin is less sure of — a book found by
title and author rather than by path — arrive unticked and say so.

---

## Collections

Collection names can come from a custom column (the default,
`#pb_collections`), from calibre **tags**, from the **series** name, or from a
**template**. There is a preview button so you can see what any source would
produce before using it.

---

## Safety

- **Nothing is ever deleted on the device.** The plugin contains no `DELETE`
  or `DROP` statement at all, and a unit test enforces that. Removing a book
  from a collection sets `is_deleted`, which is what the firmware itself does.
- **A backup is taken before every write**, to
  `<calibre config>/plugins/pocketbook_utilities/backups/<serial>/`. The
  newest ten are kept, plus the very first one ever taken.
- **Reads never touch the device.** The database is in WAL mode, so a plain
  file copy can read stale data; the plugin takes a consistent snapshot with
  SQLite's online backup API and works on that. A test asserts the device
  file is byte-identical after a read.
- **Writes are one short transaction** on the device, so a cable pulled
  mid-write cannot leave a half-written database.
- **All SQL is parameterised.** Apostrophes in titles, authors and collection
  names are ordinary data, not a crash.

### Why ejecting matters

PocketBook can rewrite its library database from memory when the USB
connection drops, silently undoing anything written while it was plugged in.

After every write the plugin records a receipt, and on the next connection it
checks whether those changes are still there, telling you plainly if they
were reverted. There is also an *Eject automatically after writing* option on
the Device tab.

**Eject the reader from calibre before unplugging it.**

---

## Troubleshooting

**No toolbar button.** calibre's default toolbar holds built-in actions only;
no plugin appears there unless added. Resetting your toolbars to defaults
removes it again. The plugin offers to add one on startup — or do it
yourself in Preferences → Toolbars & menus → *The main toolbar* **and** *The
main toolbar when a device is connected*.

**"calibre has not loaded the new columns yet".** Expected right after
creating columns: calibre only picks up new columns when it restarts.
Restart and sync again.

**Nothing is proposed.** That is the normal result when both sides already
agree. The summary line tells you how many books were matched.

**Books are not matched.** Open *Books on the device not in this library*.
Books calibre did not put on the reader are never touched. If calibre lists a
book as being on the device but the reader's database does not know it, the
reader has not re-scanned its library yet.

**Changes disappeared after unplugging.** See *Why ejecting matters*. Use
*Verify the last write* to confirm, then sync again and eject properly.

---

## How it works

Worth knowing if you want to extend this. The device database lives at
`system/explorer-3/explorer-3.db` and all reading state is in one table:

```sql
books_settings(bookid, profileid, position TEXT, position_ts INTEGER,
               cpage INTEGER, npage INTEGER, opentime INTEGER,
               completed INTEGER, completed_ts INTEGER,
               favorite INTEGER, favorite_ts INTEGER, custom TEXT,
               PRIMARY KEY(bookid, profileid))
```

Books are matched to calibre by resolving each storage root against the
filesystem, then by exact relative path, falling back to filename + size,
filename alone, and finally title + author — each match tagged with a
confidence.

Three firmware details shape the design:

```sql
-- completed_ts is not ours to write: any UPDATE of completed, and any
-- INSERT at all, makes the device stamp it with the current time.
CREATE TRIGGER completed_ts_update AFTER UPDATE ON books_settings
  WHEN NEW.completed <> OLD.completed BEGIN ... END

-- the full-text search index maintains itself when title or author change,
-- so fixing metadata keeps on-device search correct for free.
CREATE TRIGGER trigger_on_update_books_impl AFTER UPDATE ON books_impl
  WHEN NEW.title != OLD.title OR NEW.author != OLD.author BEGIN ... END

-- and a collection name is UNIQUE, so an existing but soft-deleted shelf
-- must be undeleted, never re-inserted.
CREATE TABLE bookshelfs(..., UNIQUE(name) ON CONFLICT FAIL)
```

---

## Development

Everything under `device/` and `sync/` is free of Qt and of the calibre GUI,
so it is unit-testable headlessly. Run the tests with calibre's own Python:

```bash
calibre-debug -e tests/run_tests.py
```

Build the installable zip:

```bash
calibre-debug -e build.py
```

`tests/schema_v39.sql` is dumped verbatim from a real device, triggers and
indexes included, and the fixtures are built from it. One test asserts the
fixture's schema fingerprint still matches a real device, so firmware drift
surfaces as a test failure rather than a mystery at runtime. Others enforce
the project's rules: no destructive SQL, no SQL built by concatenation, no
silently swallowed exceptions, and an idempotence test that fails if a second
sync proposes anything.

```
device/     detection, schema introspection, safe database access, backups
sync/       identity matching, the merge engine, collections, metadata push
common/     dialogs and small shared helpers
action.py   toolbar menu and dispatch
runner.py   read device -> build plan -> apply
columns.py  the custom columns, and creating them in one pass
```

---

## Credits and licence

Written for a PocketBook InkPad 3 Color. The architecture follows the shape
of [Kobo Utilities](https://github.com/janlarres/kobo-utilities), which is
worth reading if you are building something similar. Knowledge of the
PocketBook schema started from
[Pocketbook Collections](https://github.com/Anareaty/pocketbook-collections)
by Anareaty.

Released under the **GNU GPL v3**, matching calibre.

*No warranty. It takes a backup before every write, but you are the one
holding the e-reader.*
