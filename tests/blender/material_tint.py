"""Diffuse modulation, texture replacement and the reported GrNLa blue material."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, surface
from melee_map_editor.protocol import read, run
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
with tempfile.TemporaryDirectory(prefix='mme-tint-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    info = next(i for i in stage['editableMeshes'] if (i['groupIndex'],i['jobjIndex'],i['dobjIndex']) == (3,4,0))
    entry = next(m for m in stage['modelMaterials'] if m['id'] == info['id'])
    assert [round(c*255) for c in entry['preview']['color'][:3]] == [12,25,76]
    assert entry['preview']['texture']['colorOperation'] == 4
    assert not entry['preview']['useVertexColor']
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    material = modeling.target_object(s, info).active_material
    nodes = material.node_tree.nodes
    tint = nodes['Stage Diffuse Tint']
    assert tint.blend_type == 'MULTIPLY'
    assert tint.inputs[2].links[0].from_node == nodes['Stage Texture']
    assert all(abs(a-linear(b/255)) < 1e-7 for a,b in zip(tint.inputs[1].default_value, (12,25,76)))
    blend_info = next(i for i in stage['editableMeshes'] if (i['groupIndex'],i['jobjIndex'],i['dobjIndex']) == (3,3,3))
    blend_entry = next(m for m in stage['modelMaterials'] if m['id'] == blend_info['id'])
    assert [round(c*255) for c in blend_entry['preview']['color'][:3]] == [38,25,25]
    assert blend_entry['preview']['texture']['colorOperation'] == 3
    assert blend_entry['preview']['texture']['colorBlend'] == .25
    blend_node = modeling.target_object(s, blend_info).active_material.node_tree.nodes['Stage Diffuse Tint']
    assert blend_node.blend_type == 'MIX' and blend_node.inputs[0].default_value == .25
    assert all(abs(a-b/255) < 1e-7 for a,b in zip(blend_node.inputs[1].default_value, (38,25,25)))
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    modeling.target_object(s, info).data.vertices[0].co.z += 1
    modeling.target_object(s, blend_info).data.vertices[0].co.z += 1
    scene.apply(s, CLI, 'dotnet', tmp / 'moved.dat')
    # Render known grey texels: modulate is tinted, replace and vertex source are not.
    header = bytearray(18); header[2] = 2; header[12] = header[14] = 1; header[16] = 32; header[17] = 0x28
    (tmp / 'grey.tga').write_bytes(header + bytes([128,128,128,255]))
    texture = dict(file='grey.tga', scale=[1,1,1], rotation=[0,0,0], translation=[0,0,0],
                   wrapS=0, wrapT=0, repeatS=1, repeatT=1)
    check = bpy.data.scenes.new('Tint render')
    bpy.context.window.scene = check
    cases = [(4,False,1),(5,False,1),(4,True,1),(3,False,.25),(3,False,0),(3,False,1)]
    for i, (operation, vertex, blend) in enumerate(cases):
        mat = bpy.data.materials.new(f'Tint case {i}')
        rgb = (38,25,25) if operation == 3 else (12,25,76)
        surface.configure_preview(mat, dict(color=[*(c/255 for c in rgb),1], useVertexColor=vertex,
                                  texture=texture | dict(colorOperation=operation, colorBlend=blend)), tmp,
                                  dict(baselineFiles=[dict(file='grey.tga')]))
        mesh = bpy.data.meshes.new(f'Tint plane {i}')
        mesh.from_pydata([(i,-.5,0),(i+1,-.5,0),(i+1,.5,0),(i,.5,0)], [], [(0,1,2,3)])
        obj = bpy.data.objects.new(mesh.name, mesh); check.collection.objects.link(obj)
        mesh.materials.append(mat)
    camera = bpy.data.objects.new('Tint camera', bpy.data.cameras.new('Tint camera'))
    check.collection.objects.link(camera); camera.location = (len(cases)/2,0,10)
    camera.data.type = 'ORTHO'; camera.data.ortho_scale = len(cases); check.camera = camera
    check.render.engine = 'CYCLES'; check.cycles.device = 'CPU'; check.cycles.samples = 1
    check.render.resolution_x = len(cases)*32; check.render.resolution_y = 32; check.render.resolution_percentage = 100
    check.render.image_settings.file_format = 'OPEN_EXR'; check.render.filepath = str(tmp / 'tint.exr')
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(check.render.filepath)
    pixels = list(image.pixels)
    grey = linear(128/255)
    for i, (operation, vertex, blend) in enumerate(cases):
        expected = [grey*linear(c/255) for c in (12,25,76)] if i == 0 else [grey]*3
        if operation == 3:
            expected = [linear((1-blend)*(c/255)+blend*(128/255)) for c in (38,25,25)]
        index = (16*check.render.resolution_x + i*32 + 16)*4
        assert all(abs(a-b) < .001 for a,b in zip(pixels[index:index+3],expected)), (i,pixels[index:index+3],expected)
print('MATERIAL TINT PASS: GrNLa G003 J004 D000 and J003 D003, export preservation, rendered modulation/blend/replacement/vertex source')
