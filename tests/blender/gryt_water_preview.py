"""Yoshi's Story water uses both TObj layers and both GX UV channels."""
import os
from pathlib import Path
import sys
import tempfile
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-gryt-water-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrYt.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    group = read(tmp / 'session/models/group-001/group.json')
    nodes = {node['id']: node for node in group['nodes']}
    dobj = next(node for node in group['nodes'] if node['kind'] == 'dobj' and node['index'] == 31
                and nodes[node['ownerId']]['index'] == 8)
    pobj = next(node for node in group['nodes'] if node.get('ownerId') == dobj['id'])
    entry = next(entry for entry in stage['modelPreviews'] if entry['id'] == pobj['id'])
    textures = entry['preview']['textures']
    assert entry['preview']['warning'] is None
    assert len(textures) == 2
    assert [texture['texCoord'] for texture in textures] == [0, 1]
    assert [texture['colorOperation'] for texture in textures] == [3, 3]
    assert abs(textures[0]['colorBlend'] - .548023) < 1e-6
    assert abs(textures[1]['colorBlend'] - .581921) < 1e-6

    scene.import_session(bpy.context, tmp / 'session')
    obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == pobj['id'])
    assert list(obj.data.uv_layers.keys()) == ['UVMap', 'UVMap.001']
    assert obj.data.uv_layers['UVMap'].data[0].uv != obj.data.uv_layers['UVMap.001'].data[0].uv
    material = obj.active_material
    nodes = material.node_tree.nodes
    first, second = nodes['Stage Texture'], nodes['Stage Texture 2']
    assert nodes['Stage UV'].uv_map == 'UVMap'
    assert nodes['Stage UV 2'].uv_map == 'UVMap.001'
    assert first.image and second.image and first.image != second.image
    first_blend, second_blend = nodes['Stage Diffuse Tint'], nodes['Stage Texture Blend 2']
    assert first_blend.blend_type == second_blend.blend_type == 'MIX'
    assert abs(first_blend.inputs[0].default_value - .548023) < 1e-6
    assert abs(second_blend.inputs[0].default_value - .581921) < 1e-6
    assert second_blend.inputs[1].links[0].from_node == first_blend
    assert material.node_tree.nodes['Stage Alpha Surface']

print('GRYT WATER PREVIEW PASS: both TObj blend layers and UV channels are active')
