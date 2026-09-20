"""Exercise repository loading and repeated module reload with an edited scene."""
from pathlib import Path
import os
import runpy
import tempfile
import bpy

ROOT = Path(__file__).resolve().parents[2]
loader_path = ROOT / 'scripts/load_blender_addon.py'
namespace = runpy.run_path(str(loader_path))
load = namespace['load']
module = load()
cli = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
corpus = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
with tempfile.TemporaryDirectory(prefix='mme-reload-') as directory:
    directory = Path(directory)
    from melee_map_editor.protocol import run
    run(cli, 'dotnet', 'extract', corpus / 'GrNLa.dat', '--session', directory / 'session')
    obj = module.scene.import_session(bpy.context, directory / 'session')
    obj.data.vertices[0].co.z += 1
    expected = module.scene.prepare(bpy.context.scene)[1]
    prefs = bpy.context.preferences.addons['melee_map_editor'].preferences
    prefs.cli_path = str(cli)
    for _ in range(3):
        old = module
        old_helpers = (module.scene, module.collision, module.topology, module.materials)
        module = load()
        assert module is not old
        assert all(a is not b for a, b in zip(old_helpers,
                   (module.scene, module.collision, module.topology, module.materials)))
        assert module.scene.prepare(bpy.context.scene)[1] == expected
        assert bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path == str(cli)
        handlers = [h for h in bpy.app.handlers.depsgraph_update_post
                    if h.__module__ == 'melee_map_editor']
        assert handlers == [module.update_dirty]
        frame_handlers = [h for h in bpy.app.handlers.frame_change_post
                          if h.__module__ == 'melee_map_editor']
        assert frame_handlers == [module.update_animation]
        load_handlers = [h for h in bpy.app.handlers.load_post
                         if h.__module__ == 'melee_map_editor']
        assert load_handlers == [module.load_animation]
    # Reproduce Blender Text Editor's synthetic root-level __file__ while its
    # Text datablock retains the real on-disk filepath. Use an unrelated cwd.
    area = bpy.context.screen.areas[0]
    original_type = area.type
    original_cwd = Path.cwd()
    text = bpy.data.texts.load(str(loader_path))
    try:
        os.chdir(directory)
        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = text
        with bpy.context.temp_override(area=area):
            synthetic = {'__file__': '/load_blender_addon.py', '__name__': '__main__'}
            exec(compile(loader_path.read_text(), '/load_blender_addon.py', 'exec'), synthetic)
            assert synthetic['find_repository']() == ROOT
            assert synthetic['load'].__globals__['find_repository']() == ROOT
            # A copied Text block with no filepath gets an actionable error.
            text.filepath = ''
            try:
                synthetic['find_repository']()
            except RuntimeError as exc:
                assert 'Text > Open' in str(exc)
            else:
                raise AssertionError('Missing repository should produce a clear error')
            synthetic['REPOSITORY'] = str(ROOT)
            assert synthetic['find_repository']() == ROOT
    finally:
        os.chdir(original_cwd)
        area.type = original_type
        bpy.data.texts.remove(text)
    import melee_map_editor as module
    assert module.scene.prepare(bpy.context.scene)[1] == expected
    module.scene.apply(bpy.context.scene, cli, 'dotnet', directory / 'reloaded.dat')
print('BLENDER_RELOAD_OK: checkout source, fresh helpers, preserved edits/preferences, one handler of each type, export')
