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

All of them are generated:

    & "C:\Program Files\Calibre2\calibre-debug.exe" -e docs\make_screenshots.py

The widgets are laid out off-screen, so no window appears on the desktop and
no reader has to be plugged in. Re-run it whenever a dialog changes, and
commit what comes out.

| File | Shows |
|---|---|
| `review.png` | The review dialog: changes in both directions, an already-applied pair, an unticked weak match |
| `wizard.png` | The first-run setup dialog with its column preview |
| `settings-device.png` | The settings dialog, Device tab |
| `settings-sync.png` | The settings dialog, Sync tab |
| `menu.png` | The toolbar menu with the *Send to device* submenu beside it |
| `library.png` | calibre's library view with the `PB *` columns |

The books in them are invented. Screenshots of a real library put its
contents in a public repository, and the pages only need something plausible
on screen.

Two of these are not the plugin's own widgets, which is worth knowing before
changing them:

* **menu.png** calls `rebuild_menus` unbound against a stand-in that reports
  a connected, configured device and a few selected books, so the menu is the
  real one with nothing greyed out. Qt draws a submenu as a popup of its own,
  so the two are grabbed separately and composited at the row the submenu
  belongs to.
* **library.png** builds a scratch library, runs the plugin's own
  `ensure_columns` on it, adds five books and renders calibre's `BooksView`
  against it with the unrelated columns hidden. `BooksView` reaches into
  `gui.iactions` for its context menu, which `_stub_parent` supplies.
