# The columns it creates

The plugin keeps everything it reads from the reader in ordinary calibre
custom columns, so you can sort, search, filter and build saved searches on
it like any other column. Nothing is hidden in a private store.

**Set up columns** (on the Columns tab of the settings, or from the menu)
creates all of them in one pass.

| Heading | Lookup name | Type | Direction | Holds |
|---|---|---|---|---|
| PB Read | `#pb_read` | Yes/No | both ways | Marked as finished on the reader |
| PB Read date | `#pb_read_date` | Date | reader → calibre | When the reader marked it finished |
| PB Last opened | `#pb_last_read` | Date | reader → calibre | When the book was last opened |
| PB Favourite | `#pb_favourite` | Yes/No | both ways | The reader's favourite flag |
| PB Progress | `#pb_progress` | Float, shown as `42%` | reader → calibre | Percentage read, from the device page counters |
| PB Position | `#pb_position` | Text | both ways | Reading position, an EPUB CFI wrapped in a `pbr:` URI |
| PB Collections | `#pb_collections` | Text, comma separated | both ways | Collections the book is in on the reader |

![The columns in the library view](https://raw.githubusercontent.com/Mattie112/PocketBookUtilities/main/docs/screenshots/library.png)

## Why the read date and progress only come back one way

The reader owns them. `PB Read date` is stamped by a database trigger in the
firmware whenever the finished flag changes, so whatever the plugin wrote
there would be replaced the moment the device touched the row. Progress is
derived from the device's own page counters, which only mean anything on the
device. Writing either one back would achieve nothing, so the plugin does not
offer to.

## Reusing columns you already have

If a column with the plugin's own lookup name already exists and has a usable
type, setup reuses it rather than creating a second one. That is how running
setup twice is a no-op.

Columns that merely happen to have the right type are **not** claimed. An
earlier version did that and quietly mapped read status onto an unrelated
`#read` column; it now only reuses a column carrying that field's own lookup
name. To point a field at a column of your own, use the drop-downs under
*Which column holds what* on the Columns tab — every column of a compatible
type is listed there, and `(not synced)` leaves the field alone.

A field the connected device does not support is shown greyed out with the
reason next to it.

## Removing them

The plugin never deletes a column. If you stop using it, remove the `#pb_*`
columns yourself under **Preferences → Add your own columns**. The sync
history lives separately, in
`<calibre config>/plugins/pocketbook_utilities/state.db`, and is only read
when a matching library and device are seen again.
