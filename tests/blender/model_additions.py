"""Run: blender --background --factory-startup --python-exit-code 1 --python tests/blender/model_additions.py"""
import json
import gc
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import model_additions, scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-additions-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    assert stage['capabilities']['modelAddition']
    enum_items = model_additions.target_items(None, bpy.context)
    gc.collect()
    assert enum_items is model_additions.target_items(None, bpy.context)
    assert all(identifier and name for identifier, name, _description in enum_items)
    target = stage['modelAdditionTargets'][0]['id']
    assert stage['modelAdditionTargets'][0]['placement'] == 'existing-jobj'
    source_mesh_count = sum(len(read(directory / group['file'])['meshes'])
                            for group in stage['modelGroups'])

    image = bpy.data.images.new('Asymmetric Addition', width=3, height=5, alpha=True)
    values = []
    for y in range(5):
        for x in range(3):
            values.extend(((x + 1) / 4, (y + 1) / 6, (x + y + 1) / 9, (x + 2 * y + 1) / 12))
    image.pixels = values
    textured = bpy.data.materials.new('Addition Texture')
    textured.use_nodes = True
    nodes = textured.node_tree.nodes
    bsdf = next(node for node in nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled')
    texture = nodes.new('ShaderNodeTexImage')
    texture.image = image
    texture.extension = 'REPEAT'
    texture.interpolation = 'Closest'
    texture_coordinate = nodes.new('ShaderNodeTexCoord')
    textured.node_tree.links.new(texture_coordinate.outputs['UV'], texture.inputs['Vector'])
    vertex_color = nodes.new('ShaderNodeVertexColor')
    vertex_color.layer_name = 'Color'
    multiply = nodes.new('ShaderNodeMixRGB')
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs['Fac'].default_value = 1
    textured.node_tree.links.new(texture.outputs['Color'], multiply.inputs[1])
    textured.node_tree.links.new(vertex_color.outputs['Color'], multiply.inputs[2])
    textured.node_tree.links.new(multiply.outputs['Color'], bsdf.inputs['Base Color'])
    constant = bpy.data.materials.new('Addition Constant')
    constant.diffuse_color = (0.25, 0.5, 0.75, 1)

    mesh = bpy.data.meshes.new('External Mesh')
    mesh.from_pydata([(0, 0, 0), (2, 0, 0), (0, 2, 0),
                      (3, 0, 0), (5, 0, 0), (3, 2, 0)], [],
                     [(0, 1, 2), (3, 4, 5)])
    mesh.materials.append(textured)
    mesh.materials.append(constant)
    mesh.polygons[0].material_index = 0
    mesh.polygons[1].material_index = 1
    uv = mesh.uv_layers.new(name='UVMap')
    for item, value in zip(uv.data, ((0, 0), (1, 0), (0, 1), (0, 0), (1, 0), (0, 1))):
        item.uv = value
    colors = mesh.color_attributes.new(name='Color', type='BYTE_COLOR', domain='CORNER')
    for item in colors.data:
        item.color = (1, 1, 1, 1)
    source = bpy.data.objects.new('External Source', mesh)
    bpy.context.scene.collection.objects.link(source)
    source.location = (10, -20, 30)
    source.scale = (-2, 3, 4)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.context.view_layer.update()

    source_name = source.name
    assert bpy.ops.mme.add_models(target_jobj_id=target) == {'FINISHED'}
    registered = model_additions.objects(bpy.context.scene)
    assert len(registered) == 2 and source_name not in bpy.context.scene.objects
    assert all(obj.name.startswith('Editable Model - Group ') for obj in registered)
    assert all(len(obj.users_collection) == 1
               and obj.users_collection[0].get('mme_role') == 'group' for obj in registered)
    assert all(obj.parent and obj.parent.get('mme_role') == model_additions.DOBJ_ROLE
               for obj in registered)
    assert all(obj.parent.parent_type == 'BONE' and obj.parent.parent_bone
               for obj in registered)
    assert all(obj.data.name.startswith('Group 003 Mesh')
               and 'Imported' not in obj.data.name for obj in registered)
    assert not any(collection.get('mme_role') == model_additions.COLLECTION_ROLE
                   for collection in bpy.data.collections)
    assert all(key not in obj for obj in registered for key in ('mme_target_placement',
        'mme_anchor_jobj_id', 'mme_source_name', 'mme_material_ids',
        'mme_part_ids', 'mme_image_ids'))
    assert bpy.ops.mme.edit_model() == {'FINISHED'}
    assert bpy.context.object.mode == 'EDIT'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert registered[0].data.materials[0] != textured
    registered_nodes = registered[0].data.materials[0].node_tree.nodes
    assert any(node.bl_idname == 'ShaderNodeEmission' for node in registered_nodes)
    assert not any(node.bl_idname in {'ShaderNodeBsdfPrincipled', 'ShaderNodeVertexColor'}
                   for node in registered_nodes)
    registered_texture = next(node for node in registered[0].data.materials[0].node_tree.nodes
                              if node.bl_idname == 'ShaderNodeTexImage')
    assert registered_texture.image.as_pointer() != image.as_pointer(), (
        registered_texture.image.name, image.name,
        registered_texture.image.as_pointer(), image.as_pointer())
    assert scene.protected_inventory_matches(bpy.context.scene,
        set(json.loads(bpy.context.scene['mme_model_baselines'])),
        set(json.loads(bpy.context.scene['mme_jobj_baselines'])))
    payload, assets = model_additions.edits(bpy.context.scene, stage)
    assert len(payload['additions']) == 1
    assert payload['modelAdditionSchemaVersion'] == 2
    assert payload['additions'][0]['placement'] == 'existing-jobj'
    assert len(payload['additions'][0]['parts']) == 2
    assert len(payload['materials']) == 2 and len(payload['images']) == 1
    report = json.loads(bpy.context.scene['mme_addition_report'])
    assert any('vertex-color modulation is omitted' in warning
               for warning in report['warnings']), report
    pixels = assets[payload['images'][0]['payloadPath']]
    # Raw image rows are bottom-up in Blender; the protocol payload is top-left.
    expected_red = round(values[(4 * 3) * 4] * 255)
    assert pixels[0] == expected_red, (pixels[:8], expected_red,
        registered_texture.image.colorspace_settings.name, list(registered_texture.image.pixels[:8]))
    bpy.ops.wm.save_as_mainfile(filepath=str(temporary / 'additions.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(temporary / 'additions.blend'))
    assert len(model_additions.objects(bpy.context.scene)) == 2
    reopened_payload, reopened_assets = model_additions.edits(bpy.context.scene, stage)
    assert reopened_payload == payload and reopened_assets == assets
    result = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'added.dat')
    assert result['modelChanged'] and result['modelTriangles'] == 2
    assert not list((directory / 'edits').iterdir())
    repeated = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'repeated.dat')
    assert repeated['sha256'] == result['sha256']
    assert (temporary / 'repeated.dat').read_bytes() == (temporary / 'added.dat').read_bytes()

    run(CLI, 'dotnet', 'extract', temporary / 'added.dat', '--session', temporary / 'reimport')
    reimported = read(temporary / 'reimport/stage.json')
    output_mesh_count = sum(len(read(temporary / 'reimport' / group['file'])['meshes'])
                            for group in reimported['modelGroups'])
    assert output_mesh_count == source_mesh_count + 2
    bpy.ops.object.select_all(action='DESELECT')
    pending = model_additions.objects(bpy.context.scene)
    pending[0].select_set(True)
    assert model_additions.remove_selected(bpy.context) == 1
    assert not model_additions.objects(bpy.context.scene)
    assert model_additions.edits(bpy.context.scene, stage) == (None, {})

print('model addition Blender test passed')
