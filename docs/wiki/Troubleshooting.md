# Troubleshooting

## There is no plugin button anywhere

calibre's default toolbar holds built-in actions only, so no plugin appears
there on its own, and resetting the toolbars removes any you added. The
plugin notices and offers to add its button at startup. If you dismissed
that, add it under **Preferences → Toolbars & menus → The main toolbar**.

## Everything on the menu is greyed out

Two reasons, both in the tooltip:

* *No PocketBook is connected* — the reader must be plugged in and in USB
  storage mode, showing up as a drive. The plugin detects a PocketBook by
  finding `system/explorer-N/explorer-N.db` on the drive, not by which
  calibre driver is in use, so any driver that mounts it will do.
* *Run "Set up columns" first* — the plugin has nowhere to put what it reads.

## A sync proposes no changes at all

That is the normal result when nothing has changed on either side. The plugin
remembers what both sides looked like last time and only proposes real
differences — see [How syncing works](How-syncing-works).

If you expected changes, check that the field is ticked under *What to keep
in step*, and that it has a column on the Columns tab.

## A sync proposes far too many changes

Usually the remembered state is missing or was cleared, so every difference
looks new. The review dialog is the safety net here: look at the direction
column before applying.

## Some books are not matched

Books the reader has that calibre did not put there are deliberately left
alone; **Books on the device not in this library…** lists them.

Books that moved on disk since calibre last looked may fall back to a weaker
match. Weak matches are listed unticked rather than hidden; *Pre-tick changes
for* on the Sync tab controls where that line sits.

## Changes came back after unplugging

PocketBook can rewrite its database from memory when the USB connection
drops. Turn on *Eject the device automatically after writing to it*, and use
**Verify the last write…** to see exactly which values were reverted and
re-apply them. See [Backups and safety](Backups-and-safety).

## Collections do not appear on the reader

Check the Device tab's capability list first: a firmware without a shelves
table has those menu entries greyed out.

Otherwise it is usually the source setting. Only *A custom column* is
two-way; tags, series and templates are read from calibre and never written
back. See [Collections](Collections).

## Fixing authors changed nothing

The device probably has the same bad value calibre has. Fix the metadata in
calibre first. [Fixing author sorting](Fixing-author-sorting) explains how to
tell.

## Reporting a problem

Three things, in this order:

1. **Show device information…** from the menu — model, schema version and
   capability flags. It contains no book titles and no file paths.
2. **Copy diagnostics to the clipboard**, on the Advanced tab.
3. What you did, what you expected, and what happened.

For a crash, tick *Write diagnostic messages to the log* on the Advanced tab,
start calibre with `calibre-debug -g`, reproduce it, and include the output.

[Issue tracker](https://github.com/Mattie112/PocketBookUtilities/issues) ·
[MobileRead thread](https://www.mobileread.com/forums/showthread.php?t=376388)

## A different PocketBook model

The plugin reads the database schema at connect time and turns off anything
the firmware has no table for, so a different model should either work or
degrade visibly rather than break. It was developed against an InkPad 3
Color on schema version 39.

If yours is different, please post **Show device information…** output — it
is how the compatibility list gets longer.
