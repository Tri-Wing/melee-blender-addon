"""Melee material panel values drive preview and copied DAT materials."""
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, material_properties
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
with tempfile.TemporaryDirectory(prefix='mme-property-panel-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    info = next(i for i in stage['editableMeshes'] if (i['groupIndex'],i['jobjIndex'],i['dobjIndex']) == (3,3,3))
    obj = modeling.target_object(s, info)
    material = obj.active_material
    assert material_properties.definition(material)['canEditBlend']
    assert material_properties.edits(s, stage) is None
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    rgb = [30, 100, 210]
    material.mme_diffuse = [material_properties.linear(c / 255) for c in rgb]
    material.mme_alpha = .375
    material.mme_texture_blend = .75
    assert not material.get('mme_material_error'), material.get('mme_material_error')
    node = material.node_tree.nodes['Stage Diffuse Tint']
    assert node.inputs[0].default_value == .75
    assert all(abs(a-b/255)<1e-6 for a,b in zip(node.inputs[1].default_value,rgb))
    assert json.loads(material['mme_alpha_preview'])['material'] == .375
    values = material_properties.edits(s, stage)['materials']
    assert values == [{'id': info['id'], 'diffuse': rgb, 'alpha': .375, 'textureBlend': .75}]
    result = scene.apply(s, CLI, 'dotnet', tmp / 'properties.dat')
    assert result['materialChanged'] and not result['modelChanged']
    run(CLI, 'dotnet', 'extract', tmp / 'properties.dat', '--session', tmp / 'exported')
    exported = read(tmp / 'exported/stage.json')
    new_info = next(i for i in exported['editableMeshes'] if (i['groupIndex'],i['jobjIndex'],i['dobjIndex']) == (3,3,3))
    definition = next(i for i in exported['editableMaterialProperties'] if i['id'] == new_info['id'])
    assert definition['diffuse'] == rgb and definition['alpha'] == .375 and definition['textureBlend'] == .75
    # Same imported material assigned to another model shares exported edits.
    other_info = next(i for i in stage['editableMeshes'] if (i['groupIndex'],i['jobjIndex'],i['dobjIndex']) == (3,4,0))
    other = modeling.target_object(s, other_info)
    from melee_map_editor import surface
    surface.assign(other, material)
    obj.data.vertices[0].co.z += 1
    scene.apply(s, CLI, 'dotnet', tmp / 'combined.dat')
    assert not list((directory / 'edits').iterdir())
    # Conflicting Blender copies cannot silently overwrite each other.
    copied = material.copy()
    other.data.materials[0] = copied
    copied.mme_alpha = .5
    try:
        material_properties.edits(s, stage)
        raise AssertionError('Expected conflicting material copies to fail')
    except StageError as error:
        assert 'conflicting' in str(error)
    other.data.materials[0] = material
    bpy.data.materials.remove(copied)
    animated = next(i for i in stage['editableMeshes'] if i.get('positionsOnly'))
    assert material_properties.definition(modeling.target_object(s, animated).active_material) is None
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'properties.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'properties.blend'))
    runpy.run_path(str(ROOT / 'scripts/load_blender_addon.py'), run_name='__main__')
    from melee_map_editor import scene, modeling, material_properties
    s = bpy.context.scene
    material = modeling.target_object(s, info).active_material
    assert material.mme_alpha == .375 and material.mme_texture_blend == .75
    scene.apply(s, CLI, 'dotnet', tmp / 'reopened.dat')
    assert (tmp / 'combined.dat').read_bytes() == (tmp / 'reopened.dat').read_bytes()
    material.mme_texture_blend = .5
    assert material.node_tree.nodes['Stage Diffuse Tint'].inputs[0].default_value == .5
print('MATERIAL PROPERTIES PASS: preview, export, shared assignment, conflicts, save/reload, animated protection')
