"""Preview toggle, material rebuild, save/reload, and no-edit DAT preservation."""
from pathlib import Path
import json
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor as addon
from melee_map_editor import scene, surface
from melee_map_editor.protocol import run

addon.register()
cli = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
source = ROOT / 'example_assets/GrNLa.dat'


def preview_materials():
    return {slot.material for obj in bpy.context.scene.objects for slot in obj.material_slots
            if slot.material and slot.material.get('mme_alpha_preview')}


with tempfile.TemporaryDirectory(prefix='mme-dithering-') as temporary:
    directory = Path(temporary)
    run(cli, 'dotnet', 'extract', source, '--session', directory / 'session')
    scene.import_session(bpy.context, directory / 'session')
    current = bpy.context.scene
    assert not current.mme_dithered_transparency
    materials = preview_materials()
    original = {m.name: m.surface_render_method for m in materials}
    assert 'BLENDED' in original.values() and 'DITHERED' in original.values()
    graphs = {m.name: (len(m.node_tree.nodes), len(m.node_tree.links)) for m in materials}
    unrelated = bpy.data.materials.new('Unrelated material')
    unrelated.surface_render_method = 'BLENDED'
    current.mme_dithered_transparency = True
    assert all(m.surface_render_method == 'DITHERED' for m in materials)
    assert unrelated.surface_render_method == 'BLENDED'
    assert graphs == {m.name: (len(m.node_tree.nodes), len(m.node_tree.links)) for m in materials}
    # Material edits rebuild alpha nodes and must retain the enabled option.
    rebuilt = next(m for m in materials if original[m.name] == 'BLENDED')
    surface.configure_alpha_preview(rebuilt, json.loads(rebuilt['mme_alpha_preview']))
    assert rebuilt.surface_render_method == 'DITHERED'
    current.frame_set(30)
    assert all(m.surface_render_method == 'DITHERED' for m in materials)
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / 'preview.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(directory / 'preview.blend'))
    current = bpy.context.scene
    assert current.mme_dithered_transparency
    assert all(m.surface_render_method == 'DITHERED' for m in preview_materials())
    scene.apply(current, cli, 'dotnet', directory / 'dithered.dat')
    assert (directory / 'dithered.dat').read_bytes() == source.read_bytes()
    current.mme_dithered_transparency = False
    assert original == {m.name: m.surface_render_method for m in preview_materials()}
    scene.apply(current, cli, 'dotnet', directory / 'normal.dat')
    assert (directory / 'normal.dat').read_bytes() == source.read_bytes()

addon.unregister()
print('BLENDER_PREVIEW_DITHERING_OK')
