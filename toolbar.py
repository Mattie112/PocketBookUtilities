"""Make sure the plugin has somewhere to be clicked.

calibre's default toolbar layout is a fixed list of built-in actions; no
plugin ever appears in it. The GUI's "Load plugin from file" offers to add a
button, but installing from the command line with ``calibre-customize -a``
does not, and resetting the toolbars to their defaults removes it again.

Either way the plugin ends up loaded, working, and completely unreachable --
so it checks for itself and offers to fix it rather than leaving the user to
work out what happened.
"""

from .common.compat import debug

ACTION_NAME = 'PocketBook Utilities'

#: The two layouts a library-level action belongs on.
TOOLBAR_KEYS = ('action-layout-toolbar', 'action-layout-toolbar-device')

#: Checked as well, so a user who deliberately moved the button into a menu
#: is not nagged about it.
OTHER_KEYS = ('action-layout-menubar', 'action-layout-menubar-device',
              'action-layout-context-menu',
              'action-layout-context-menu-device')


def _layout(gprefs, key):
    try:
        return list(gprefs[key] or ())
    except Exception as err:
        debug('could not read %s: %s' % (key, err))
        return []


def placements(gprefs, name=ACTION_NAME):
    """Which layouts currently contain our action."""
    found = []
    for key in TOOLBAR_KEYS + OTHER_KEYS:
        if name in _layout(gprefs, key):
            found.append(key)
    return found


def is_reachable(gprefs, name=ACTION_NAME):
    """Can the user get at the plugin at all?"""
    return bool(placements(gprefs, name))


def add_to_toolbars(gprefs, name=ACTION_NAME):
    """Put the action on both toolbars. Returns the layouts changed."""
    changed = []
    for key in TOOLBAR_KEYS:
        current = _layout(gprefs, key)
        if name in current:
            continue
        current.append(name)
        gprefs[key] = tuple(current)
        changed.append(key)
    debug('added %r to %r' % (name, changed))
    return changed


def refresh_bars(gui):
    """Rebuild calibre's toolbars so the button appears without a restart."""
    manager = getattr(gui, 'bars_manager', None)
    if manager is None:
        return False
    try:
        manager.init_bars()
        manager.update_bars()
        return True
    except Exception as err:
        debug('could not rebuild the toolbars live: %s' % err)
        return False


def ensure_button(gui, name=ACTION_NAME):
    """Add the button if it is missing. Returns a message, or ``None``.

    Safe to call repeatedly: it does nothing once the action is placed
    anywhere the user can reach it.
    """
    from calibre.gui2 import gprefs

    if is_reachable(gprefs, name):
        return None
    changed = add_to_toolbars(gprefs, name)
    if not changed:
        return None
    if refresh_bars(gui):
        return 'A %s button was added to the toolbar.' % name
    return ('A %s button was added to the toolbar. Restart calibre to see it.'
            % name)
