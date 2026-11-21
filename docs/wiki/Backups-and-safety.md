# Backups and safety

The plugin writes directly to the reader's `explorer-3.db`. This page is what
it does so that is not frightening.

## Backups

A backup is taken **before every write to the device**, automatically. There
is no option to turn that off.

They live in:

    <calibre config>/plugins/pocketbook_utilities/backups/<storage id>/

Named after the moment they were taken. *Backups to keep* on the Device tab
sets how many are kept per device (10 by default); the very first backup ever
taken for a device is always kept on top of that.

**Back up the device database now** takes one on demand. **Restore a
backup…** puts one back.

Backups are made with SQLite's backup API rather than by copying the file.
The reader's database is in write-ahead-log mode, so a plain file copy reads
stale data and silently loses whatever is in the `-wal`.

## How writes are made

* Read from a snapshot taken locally, so nothing is analysed over USB.
* Write in one short transaction, with every value as a bound parameter.
  No SQL is built by string concatenation, so an apostrophe in a title or a
  collection name is just an apostrophe.
* **No `DELETE` statement exists anywhere in the plugin**, and a test fails
  the build if one appears. Removing a book from a collection flags the row
  as deleted, which is what the firmware itself does.
* A failing write leaves the device database byte for byte as it was.

## The device changing things back

PocketBook can rewrite its database from memory when the USB connection
drops, undoing changes that were just written.

Two things help:

* **Eject the device automatically after writing to it** (Device tab, off by
  default) ejects before that can happen.
* **Verify the last write…** re-reads the exact rows and values the last
  write touched and reports any the device has since changed back, with a
  one-click re-apply.

The receipt records the specific values written, not a hash of whole rows, so
reading a book after a sync — which legitimately moves the position — is not
reported as the device undoing something.

## Checking the database

**Check the device database…** runs an integrity check and reports orphaned
rows: entries pointing at books or files that no longer exist. It only
reports. It does not repair, and it does not delete.

## Things it will not do

* It will not touch books on the reader that calibre did not put there.
* It will not clear a value on either side just because the other side is
  empty, unless you turn that on explicitly.
* It will not write anything from a title-and-author-only match without
  showing it to you first.
