"""Green Greens keeps its color texture separate from its grayscale specular map."""
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

with tempfile.TemporaryDirectory(prefix='mme-grgb-lightmap-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrGb.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    group = read(tmp / 'session/models/group-001/group.json')
    nodes_by_id = {node['id']: node for node in group['nodes']}
    targets = []
    for dobj_index in (0, 1):
        dobj = next(node for node in group['nodes'] if node['kind'] == 'dobj'
                    and node['index'] == dobj_index
                    and nodes_by_id[node['ownerId']]['kind'].endswith('jobj')
                    and nodes_by_id[node['ownerId']]['index'] == 0)
        targets.append(next(node for node in group['nodes']
                            if node['kind'] == 'pobj' and node.get('ownerId') == dobj['id']))

    for target in targets:
        entry = next(entry for entry in stage['modelPreviews'] if entry['id'] == target['id'])
        assert [layer['lightmapFlags'] for layer in entry['preview']['textures']] == [0x10, 0x20]

    scene.import_session(bpy.context, tmp / 'session')
    for target in targets:
        obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == target['id'])
        nodes = obj.active_material.node_tree.nodes
        diffuse_texture, specular_texture = nodes['Stage Texture'], nodes['Stage Texture 2']
        diffuse = nodes['Stage Diffuse Lighting']
        assert diffuse.inputs[1].links[0].from_node == diffuse_texture

        specular_color = nodes['Stage Specular Color']
        decode = specular_color.inputs[1].links[0].from_node
        specular_stage = decode.inputs['Color'].links[0].from_node
        assert specular_stage == nodes['Stage Specular Texture']
        encode = specular_stage.inputs[2].links[0].from_node
        assert encode.inputs['Color'].links[0].from_node == specular_texture

        diffuse_pixels = list(diffuse_texture.image.pixels)
        specular_pixels = list(specular_texture.image.pixels)
        assert max(abs(diffuse_pixels[i] - diffuse_pixels[i + 1])
                   for i in range(0, len(diffuse_pixels), 4)) > .05
        assert max(abs(specular_pixels[i] - specular_pixels[i + 1])
                   for i in range(0, len(specular_pixels), 4)) < .05

    metal_group = read(tmp / 'session/models/group-002/group.json')
    metal_nodes_by_id = {node['id']: node for node in metal_group['nodes']}
    metal_targets = []
    for jobj_index, dobj_index in ((52, 7), (53, 4)):
        dobj = next(node for node in metal_group['nodes'] if node['kind'] == 'dobj'
                    and node['index'] == dobj_index
                    and metal_nodes_by_id[node['ownerId']]['index'] == jobj_index)
        metal_targets.append(next(node for node in metal_group['nodes']
                                  if node['kind'] == 'pobj' and node.get('ownerId') == dobj['id']))

    expected = [([1], [0x80]), ([0, 1], [0x10, 0x80])]
    for target, (coordinates, lightmaps) in zip(metal_targets, expected):
        entry = next(entry for entry in stage['modelPreviews'] if entry['id'] == target['id'])
        assert entry['preview']['warning'] is None
        assert [layer['coordinateType'] for layer in entry['preview']['textures']] == coordinates
        assert [layer['lightmapFlags'] for layer in entry['preview']['textures']] == lightmaps

    for target, reflection_index in zip(metal_targets, (0, 1)):
        obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == target['id'])
        nodes = obj.active_material.node_tree.nodes
        suffix = '' if reflection_index == 0 else ' 2'
        reflection = nodes[f'Stage Reflection Coordinates{suffix}']
        camera_normal = nodes[f'Stage Reflection Camera Normal{suffix}']
        assert camera_normal.vector_type == 'NORMAL'
        assert camera_normal.convert_from == 'WORLD' and camera_normal.convert_to == 'CAMERA'
        assert reflection.inputs[0].links and reflection.inputs[1].links
        texture = nodes['Stage Texture' if reflection_index == 0 else 'Stage Texture 2']
        assert texture.inputs['Vector'].is_linked
        assert nodes['Stage Extension Texture']

print('GRGB LIGHTMAP PREVIEW PASS: diffuse/specular roles and reflection-mapped metal materials')
