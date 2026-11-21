# docs

Not shipped in the plugin zip. `build.py` skips this directory.

* `wiki/` — the pages published to the
  [GitHub wiki](https://github.com/Mattie112/PocketBookUtilities/wiki).
* `screenshots/` — images used by the wiki and the main README.
* `make_screenshots.py` — regenerates most of those images.

The wiki pages are published from here automatically; see below.

## Publishing the wiki

Automatic. `.github/workflows/publish-wiki.yml` copies `docs/wiki/*.md` into
the wiki's own repository whenever `docs/wiki` changes on `main`, so merging a
pull request is all it takes. It can also be run by hand from the Actions tab.

Keeping the source here means a change to the plugin and the change to its
documentation go through one review, and the wiki cannot quietly drift away
from the code. The cost is that the wiki is a *copy*: a page edited in the
browser is overwritten on the next publish. `Home.md` and the sidebar both say
so.

The workflow pushes with the automatic `GITHUB_TOKEN` and a `contents: write`
permission. No personal access token and no repository secret are involved;
a PAT would only be needed to write into some *other* repository's wiki.

Page names come from the filenames: `How-syncing-works.md` becomes the page
*How syncing works* at `/wiki/How-syncing-works`, which is what the links
between pages use. `_Sidebar.md` is the navigation shown beside every page.
Markdown deleted from `docs/wiki` is deleted from the wiki too.

Wiki pages reference images by their full raw URL, because a wiki page cannot
see this repository's files. The main README uses a relative path.

### Starting a wiki from scratch

The wiki repository does not exist until one page has been saved through the
web interface, and the workflow cannot create it. If the clone step ever fails
with a repository-not-found error, that is why: open the repository's **Wiki**
tab, **Create the first page**, save anything, and re-run the workflow. The
placeholder is replaced by `Home.md` on the first publish.

An empty wiki is also invisible from outside — GitHub hides the Wiki tab and
serves the repository home page at `/wiki` to anyone who cannot create pages,
which makes a wiki that exists but has no pages look like one that is switched
off.

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
