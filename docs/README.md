# docs

Not shipped in the plugin zip. `build.py` skips this directory.

* `wiki/` — the pages published to the
  [GitHub wiki](https://github.com/Mattie112/PocketBookUtilities/wiki).
* `screenshots/` — images used by the wiki and the main README.
* `make_screenshots.py` — regenerates most of those images.

## Publishing the wiki

The wiki is a separate git repository. It has to exist before it can be
cloned, so if there is no wiki yet, create any one page through the web
interface first.

    git clone https://github.com/Mattie112/PocketBookUtilities.wiki.git
    cp docs/wiki/*.md PocketBookUtilities.wiki/
    cd PocketBookUtilities.wiki
    git add -A && git commit -m "Update wiki" && git push

Page names come from the filenames: `How-syncing-works.md` becomes the page
*How syncing works* at `/wiki/How-syncing-works`, which is what the links
between pages use. `_Sidebar.md` is the navigation shown beside every page.

Keeping the source here means the wiki is reviewable in a pull request and
cannot drift away unnoticed. Edit it here, then copy it over.

Wiki pages reference images by their full raw URL, because a wiki page cannot
see this repository's files. The main README uses a relative path.

## Screenshots

### Generated

    & "C:\Program Files\Calibre2\calibre-debug.exe" -e docs\make_screenshots.py

Builds the plugin's own dialogs with invented books and grabs each one. The
widgets are laid out off-screen, so no window appears and no reader has to be
plugged in. Re-run it when a dialog changes.

| File | Shows |
|---|---|
| `review.png` | The review dialog, with changes in both directions, an already-applied pair and an unticked weak match |
| `wizard.png` | The first-run setup dialog with its column preview |
| `settings-device.png` | The settings dialog, Device tab |
| `settings-sync.png` | The settings dialog, Sync tab |

The books in them are made up. Screenshots of a real library put its contents
in a public repository, and the pages only need something plausible on
screen.

### Still to be taken by hand

These are calibre showing its own windows, which the script cannot build.
Drop the file into `screenshots/` and replace the matching
`<!-- screenshot: ... -->` comment in the page with an image tag.

| File | Shot | Wanted by |
|---|---|---|
| `menu.png` | The toolbar button's drop-down, open, with the reader connected so nothing is greyed out. One submenu expanded if you can manage it. | `Home`, `Menu-reference` |
| `columns.png` | Four or five library rows with the `PB *` columns showing — read ticks, a progress percentage, a collection or two. | `Columns` |

Crop to the window rather than the whole desktop. These two will show real
book titles, so use books you are happy to have in a public repository.
