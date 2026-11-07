# PocketBook Utilities

A calibre plugin for PocketBook e-readers, written against a PocketBook
InkPad 3 Color (firmware database schema version 39).

It keeps reading status, reading position, progress, favourites and
collections in step between the reader and a calibre library, and can repair
the author information the reader sorts by.

## What it does

| | |
|---|---|
| **Read status** | `books_settings.completed` ↔ `#pb_read`, both directions |
| **Read date** | when the reader marked it finished → `#pb_read_date` |
| **Last opened** | `opentime` → `#pb_last_read` |
| **Favourites** | `favorite` ↔ `#pb_favourite`, both directions |
| **Progress** | `cpage`/`npage` → `#pb_progress` (a percentage) |
| **Reading position** | `position` ↔ `#pb_position` (a `pbr:` URI wrapping an EPUB CFI) |
| **Collections** | `bookshelfs`/`bookshelfs_books` ↔ `#pb_collections`, both directions |
| **Device metadata** | push calibre's title/author sort/series into `books_impl` |

Plus: automatic backups, restore, a database integrity check, a "books on
the device that aren't in this library" report, and a check that the reader
actually kept the last thing written to it.

## Getting started

1. **Preferences → Plugins → Load plugin from file** and pick
   `PocketBook Utilities.zip`, or from a terminal:
   ```bash
   "C:\Program Files\Calibre2\calibre-customize.exe" -a "PocketBook Utilities.zip"
   ```
2. Restart calibre.
3. Connect the reader. The first time a new device is seen, a short setup
   dialog appears: press **Set up columns** once and every custom column the
   plugin needs is created. Restart calibre so the new columns appear.
4. **Sync now (review changes)** shows everything it proposes before writing
   a single value. Nothing reaches calibre or the reader until you press
   Apply.

## How conflicts are settled

The plugin remembers what each side looked like after the last successful
sync, in its own small database under
`<calibre config>/plugins/pocketbook_utilities/state.db`. That turns the
merge into a real three-way merge:

| device since last sync | calibre since last sync | what happens |
|---|---|---|
| unchanged | unchanged | nothing is proposed |
| changed | unchanged | loaded into calibre |
| unchanged | changed | sent to the device |
| both changed | | the more recent wins, flagged in the review list |

So an idle run proposes nothing at all. **Send to device** and **Load from
device** override this when you want one side to win outright.

## Safety

- **Nothing is ever deleted on the device.** The package contains no
  `DELETE` or `DROP` statement, and a unit test enforces that. Removing a
  book from a collection sets `is_deleted`, which is what the firmware
  itself does.
- **A backup is taken before every write**, into
  `<calibre config>/plugins/pocketbook_utilities/backups/<serial>/`. The
  newest ten are kept, plus the very first one ever taken.
- **Reads never touch the device.** The database is in WAL mode, so a plain
  file copy would read stale data; the plugin takes a consistent snapshot
  with SQLite's online backup API and works on that.
- **Writes are one short transaction** directly on the device, so an
  unplugged cable cannot leave a half-written database.
- **All SQL is parameterised.** Apostrophes in titles, authors and
  collection names are ordinary data.
- **Eject before unplugging.** PocketBook can rewrite its library database
  from memory when the USB connection drops, undoing changes made while it
  was connected. After every write the plugin records a receipt and checks
  on the next connection whether the changes are still there, telling you if
  they were reverted. There is also an "eject automatically after writing"
  option on the Device tab.

## Development

Everything under `device/` and `sync/` is free of Qt and of the calibre GUI,
so it can be unit-tested headlessly. Run the tests with calibre's own Python:

```bash
"C:\Program Files\Calibre2\calibre-debug.exe" -e tests/run_tests.py
```

Build the installable zip:

```bash
"C:\Program Files\Calibre2\calibre-debug.exe" -e build.py
```

`tests/schema_v39.sql` is dumped verbatim from a real device, triggers and
indexes included, and the fixture is built from it. One test asserts the
fixture's schema fingerprint still matches the device, so firmware drift
shows up as a test failure rather than a mystery at runtime.

Two device triggers shape the design and are worth knowing about:

```sql
-- completed_ts is not ours to write: any UPDATE of completed, and any
-- INSERT at all, makes the device stamp it with the current time.
CREATE TRIGGER completed_ts_update AFTER UPDATE ON books_settings
  WHEN NEW.completed <> OLD.completed BEGIN ... END

-- the full-text search index maintains itself when title or author change,
-- so a metadata push keeps on-device search correct for free.
CREATE TRIGGER trigger_on_update_books_impl AFTER UPDATE ON books_impl
  WHEN NEW.title != OLD.title OR NEW.author != OLD.author BEGIN ... END
```

## Known limits

- Written against schema version 39. Other versions are not refused: every
  feature is gated on a capability flag read from the database at runtime,
  and anything unsupported is disabled with a visible reason. "Show device
  information" lists what the connected reader actually supports.
- CoolReader positions (`.cr3hist`) are not implemented. The reference device
  does not use CoolReader — EPUBs are read by the native engine, which keeps
  its position in the database. Detection is already in place, so the channel
  can be added if it is ever needed.
- Annotations and highlights (`.afl` files under `system/state/Books/`) are
  not touched.
- The device also stores a rating and a "liked" flag in `social`
  (`stars`, `liked`), which this plugin leaves alone.

## Replacing "Pocketbook Collections"

This plugin was written because that one misbehaves. If you still have it
installed, disable it before using this one: it begins every command,
including its read-only ones, by deleting `books_settings`, `books_impl`,
`social` and `booktogenre` rows for any book without a current file entry,
which permanently discards reading positions and read status for books you
have taken off the device.
