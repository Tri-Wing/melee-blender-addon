"""Green Greens keeps its color texture separate from its grayscale specular map."""
import os
from pathlib import Path
import struct
import sys
import tempfile
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import material_properties, scene
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

    lit_dobj = next(node for node in metal_group['nodes'] if node['kind'] == 'dobj'
                    and node['index'] == 1
                    and metal_nodes_by_id[node['ownerId']]['index'] == 52)
    lit_target = next(node for node in metal_group['nodes']
                      if node['kind'] == 'pobj' and node.get('ownerId') == lit_dobj['id'])
    definition = next(entry for entry in stage['editableMaterialProperties'] if entry['id'] == lit_target['id'])
    assert definition['ambient'] == [128, 128, 134]
    assert [layer['lightmapFlags'] for layer in definition['textures']] == [0x10, 0x20]
    lit_obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == lit_target['id'])
    source_mesh = read(tmp / 'session/models/group-002' /
                       next(name for name in metal_group['meshes']
                            if read(tmp / 'session/models/group-002' / name)['id'] == lit_target['id']))
    source_indices = source_mesh['triangleIndices']
    assert list(lit_obj.data.polygons[0].vertices) == [source_indices[0], source_indices[2], source_indices[1]]
    assert lit_obj.data['mme_reversed_source_faces'][0]
    assert all(polygon.use_smooth for polygon in lit_obj.data.polygons)
    source_normal = Vector(tuple(source_mesh['normals'][source_indices[0]][axis]
                                 for axis in ('x', 'z', 'y')))
    source_normal.y *= -1
    assert source_normal.dot(lit_obj.data.polygons[0].normal) > .9
    material = lit_obj.active_material
    assert len(material.mme_texture_layers) == 2
    assert [layer['mme_role'] for layer in material.mme_texture_layers] == [0x10, 0x20]
    ambient = material.node_tree.nodes['Stage Material Ambient']
    expected_ambient = [material_properties.linear(value / 255) for value in (128, 128, 134)]
    assert all(abs(a - b) < 1e-6 for a, b in zip(ambient.outputs[0].default_value, expected_ambient))
    assert material_properties.edits(bpy.context.scene, stage) is None

    material.mme_ambient = [material_properties.linear(value / 255) for value in (64, 32, 16)]
    material.mme_specular = [material_properties.linear(value / 255) for value in (10, 20, 30)]
    material.mme_shininess = 77
    material.mme_texture_layers[0].blend = .25
    material.mme_texture_layers[1].blend = .75
    edit = next(entry for entry in material_properties.edits(bpy.context.scene, stage)['materials']
                if entry['id'] == lit_target['id'])
    assert edit == {'id': lit_target['id'], 'ambient': [64, 32, 16], 'specular': [10, 20, 30],
                    'shininess': 77, 'textureBlends': [.25, .75]}
    output = tmp / 'material-edited.dat'
    scene.apply(bpy.context.scene, CLI, 'dotnet', output)
    data = output.read_bytes()
    integer = lambda offset: struct.unpack_from('>I', data, 32 + offset)[0]
    floating = lambda offset: struct.unpack_from('>f', data, 32 + offset)[0]
    mobj = integer(lit_dobj['sourceOffset'] + 8)
    material_offset = integer(mobj + 12)
    assert list(data[32 + material_offset:32 + material_offset + 3]) == [64, 32, 16]
    assert list(data[32 + material_offset + 8:32 + material_offset + 11]) == [10, 20, 30]
    assert floating(material_offset + 16) == 77
    first = integer(mobj + 8)
    second = integer(first + 4)
    assert floating(first + 0x44) == .25 and floating(second + 0x44) == .75

    group_one = read(tmp / 'session/models/group-001/group.json')
    smooth_payload = read(tmp / 'session/models/group-001' / group_one['meshes'][7])
    smooth_obj = next(obj for obj in bpy.context.scene.objects
                      if obj.get('mme_id') == smooth_payload['id'])
    assert smooth_obj.get('mme_enveloped') and smooth_payload['normals']
    assert all(polygon.use_smooth for polygon in smooth_obj.data.polygons)
    assert len(smooth_obj.data.corner_normals) == len(smooth_obj.data.loops)
    exact_normals = smooth_obj.data.attributes['Stage Normal']
    assert len(exact_normals.data) == len(smooth_obj.data.vertices)
    assert max((exact_normals.data[loop.vertex_index].vector.normalized()
                - smooth_obj.data.corner_normals[loop.index].vector).length
               for loop in smooth_obj.data.loops) > .1
    assert len(set(smooth_obj.data['mme_reversed_source_faces'])) == 2
    preview_normal = smooth_obj.active_material.node_tree.nodes['Stage Preview Normal']
    assert preview_normal.inputs[5].links[0].from_node.name == 'Stage Source Normal Normalize'

print('GRGB LIGHTMAP PREVIEW PASS: diffuse/specular roles and reflection-mapped metal materials')
