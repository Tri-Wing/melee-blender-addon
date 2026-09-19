"""Open this file in Blender's Text Editor and Run Script to load/reload the checkout.

This affects the current Blender process; it does not replace an installed ZIP.
"""
import importlib
from pathlib import Path
import os
import sys

import addon_utils
import bpy


# Optional override for a copied/pasted Text block with no saved filepath.
REPOSITORY = ""


def find_repository():
    override = REPOSITORY or os.environ.get('MELEEMAP_REPO', '')
    if override:
        candidates = [Path(bpy.path.abspath(override)).expanduser().resolve()]
    else:
        paths = []
        space = bpy.context.space_data
        text = getattr(space, 'text', None) if space and space.type == 'TEXT_EDITOR' else None
        if text and text.filepath:
            paths.append(bpy.path.abspath(text.filepath))
        # Blender may synthesize __file__ from a Text block name. Never assume
        # it has two parents, or that it refers to an actual on-disk script.
        if globals().get('__file__'):
            paths.append(bpy.path.abspath(__file__))
        paths.append(str(Path.cwd()))
        candidates = []
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            candidates.extend((resolved, *resolved.parents))
    for root in candidates:
        if (root / 'blender_addon/melee_map_editor/__init__.py').is_file():
            return root
    raise RuntimeError(
        'Cannot locate the Melee Map Editor checkout. In the Text Editor, use '
        'Text > Open to open scripts/load_blender_addon.py from the repository, '
        'or set REPOSITORY at the top of this script to the checkout folder '
        '(the folder containing blender_addon).')


def load():
    root = find_repository()
    directory = root / 'blender_addon'
    name = 'melee_map_editor'
    if not (directory / name / '__init__.py').is_file():
        raise RuntimeError(f'Add-on source not found in {directory}')

    # Unregister the currently loaded code before discarding its modules. Merely
    # reloading __init__ would leave helper modules and old callbacks in memory.
    errors = []
    addon_utils.disable(name, default_set=False, handle_error=lambda: errors.append(sys.exc_info()[1]))
    if errors:
        raise RuntimeError('Could not unregister the previous add-on; restart Blender.') from errors[0]
    for module in list(sys.modules):
        if module == name or module.startswith(name + '.'):
            del sys.modules[module]
    path = str(directory)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
    importlib.invalidate_caches()
    module = addon_utils.enable(name, default_set=True,
                                handle_error=lambda: errors.append(sys.exc_info()[1]))
    if module is None or errors:
        raise RuntimeError('Could not load the development add-on. See the console traceback.') from (errors[0] if errors else None)
    prefs = bpy.context.preferences.addons[name].preferences
    cli = root / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
    if not prefs.cli_path and cli.is_file():
        prefs.cli_path = str(cli)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    print(f'Melee Map Editor loaded from {module.__file__}')
    return module


if __name__ == '__main__':
    load()
