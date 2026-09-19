"""Packed stage textures, UV transforms, preview UI and export isolation."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, surface
from melee_map_editor.protocol import read, run
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

transform = {'scale': [1, 1, 1], 'rotation': [0, 0, 0], 'translation': [0.25, 0.1, 0],
             'repeatS': 2, 'repeatT': 3, 'wrapS': 1, 'wrapT': 1}
p = surface.preview_matrix(transform) @ Vector((0.5, 0.75, 0))
assert abs(p.x - 0.5) < 1e-6 and abs(p.y - 0.55) < 1e-6
transform['wrapT'] = 2
p = surface.preview_matrix(transform) @ Vector((0.5, 0.75, 0))
assert abs(p.y - 1.55) < 1e-6

with tempfile.TemporaryDirectory(prefix='mme-preview-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    entries = [m for m in stage['modelMaterials'] if m['usesUv']]
    assert entries
    for entry in entries:
        assert entry['preview']['warning'] is None, entry['preview']
        material = next(m for m in bpy.data.materials if surface.material_id(m) == entry['id'])
        node = material.node_tree.nodes['Stage Texture']
        texture = entry['preview']['texture']
        assert list(node.image.size) == [texture['width'], texture['height']]
        assert node.image.packed_file
        assert material.node_tree.nodes.active == node
        assert node.inputs['Vector'].is_linked
        assert any(e['file'] == texture['file'] for e in stage['baselineFiles'])
    entry = entries[0]
    obj = modeling.target_object(s, next(e for e in stage['editableMeshes'] if e['id'] == entry['id']))
    material = obj.active_material
    image = material.node_tree.nodes['Stage Texture'].image
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.mme.texture_preview() == {'FINISHED'}
    assert all(a.spaces.active.shading.type == 'MATERIAL' for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    uv_area = next(a for a in bpy.context.screen.areas if a.type != 'VIEW_3D')
    uv_area.type = 'IMAGE_EDITOR'; uv_area.ui_type = 'UV'
    surface.show_preview(bpy.context)
    assert uv_area.spaces.active.image == image
    assert modeling.edits(s, stage) is None
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'preview.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'preview.blend'))
    s = bpy.context.scene
    obj = modeling.target_object(s, next(e for e in stage['editableMeshes'] if e['id'] == entry['id']))
    material = obj.active_material
    image = material.node_tree.nodes['Stage Texture'].image
    assert image.packed_file and len(image.pixels) == image.size[0] * image.size[1] * 4
    assert modeling.edits(s, stage) is None
    scene.validate(s, CLI, 'dotnet')
    if os.environ.get('MELEEMAP_RENDER_PREVIEW'):
        check = bpy.data.scenes.new('Texture Preview Check')
        mesh = bpy.data.meshes.new('Preview Quad')
        mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)], [], [(0,1,2,3)])
        uv = mesh.uv_layers.new()
        for loop, point in zip(uv.data, [(0,0),(1,0),(1,1),(0,1)]): loop.uv = point
        plane = bpy.data.objects.new('Preview Quad', mesh); check.collection.objects.link(plane)
        mesh.materials.append(material)
        camera = bpy.data.objects.new('Preview Camera', bpy.data.cameras.new('Preview Camera'))
        check.collection.objects.link(camera); camera.location = (0,0,3)
        camera.data.type = 'ORTHO'; camera.data.ortho_scale = 2
        check.camera = camera
        check.render.engine = 'CYCLES'; check.cycles.device = 'CPU'; check.cycles.samples = 1
        check.render.resolution_x = 256; check.render.resolution_y = 256; check.render.resolution_percentage = 100
        check.view_settings.view_transform = 'Standard'
        check.render.filepath = os.environ['MELEEMAP_RENDER_PREVIEW']
        bpy.ops.render.render(scene=check.name, write_still=True)
print('BLENDER_TEXTURE_PREVIEW_OK: images, packing, UV transforms, viewport/UV editor, no-op export, save/load')
