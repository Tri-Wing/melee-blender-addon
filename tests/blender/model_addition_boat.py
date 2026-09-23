"""Local OBJ/MTL integration fixture for external-model addition."""
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
BOAT = ROOT / 'example_assets/wind_waker_boat/Salvage Boat.obj'

if not BOAT.is_file():
    print('model addition boat test skipped: local fixture is absent')
    raise SystemExit(0)

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-addition-boat-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    bpy.ops.wm.obj_import(filepath=str(BOAT))
    imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    assert imported and sum(len(obj.data.polygons) for obj in imported) > 0
    source_images = {node.image for obj in imported for material in obj.data.materials
                     if material and material.use_nodes for node in material.node_tree.nodes
                     if node.bl_idname == 'ShaderNodeTexImage' and node.image}
    assert len(source_images) == 1
    source_image = next(iter(source_images))
    width, height = source_image.size
    source_values = list(source_image.pixels[:])
    expected_pixels = bytearray(width * height * 4)
    for output_y in range(height):
        input_y = height - 1 - output_y
        for x in range(width):
            source = (input_y * width + x) * source_image.channels
            target = (output_y * width + x) * 4
            expected_pixels[target:target + 4] = bytes(
                round(max(0, min(1, source_values[source + channel])) * 255)
                if channel < source_image.channels else 255 for channel in range(4))
    target = next(item for item in stage['modelAdditionTargets']
                  if item['placement'] == 'new-jobj-chain')
    registered = model_additions.register_selected(bpy.context, target['id'])
    registered_images = [node.image for obj in registered for material in obj.data.materials
                         if material and material.use_nodes for node in material.node_tree.nodes
                         if node.bl_idname == 'ShaderNodeTexImage' and node.image]
    assert registered_images and all(image.packed_file for image in registered_images)
    payload, assets = model_additions.edits(bpy.context.scene, stage)
    assert len(payload['additions']) == len(imported)
    assert all(item['placement'] == 'new-jobj-chain' for item in payload['additions'])
    assert all(item['preset'] == 'opaque-diffuse-texture-v2' for item in payload['materials'])
    assert len(payload['images']) == 1 and assets
    assert next(iter(assets.values())) == expected_pixels
    bpy.ops.wm.save_as_mainfile(filepath=str(temporary / 'boat.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(temporary / 'boat.blend'))
    reopened_payload, reopened_assets = model_additions.edits(bpy.context.scene, stage)
    assert reopened_payload == payload and reopened_assets == assets
    result = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'boat.dat')
    assert result['modelChanged'] and result['modelTriangles'] > 0
    run(CLI, 'dotnet', 'extract', temporary / 'boat.dat', '--session', temporary / 'reimport')
    decoded_match = False
    for path in (temporary / 'reimport/models/textures').glob('*.tga'):
        data = path.read_bytes()
        if len(data) != 18 + len(expected_pixels):
            continue
        bgra = data[18:]
        rgba = bytearray(bgra)
        for index in range(0, len(rgba), 4):
            rgba[index], rgba[index + 2] = rgba[index + 2], rgba[index]
        decoded_match |= rgba == expected_pixels
    assert decoded_match, 'Re-imported DAT does not contain the source boat texture bytes.'

print('model addition boat integration test passed')
