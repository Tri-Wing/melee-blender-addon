"""Dream Land's Whispy overlay uses a custom TEV stage to tint an intensity texture black."""
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, surface
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
linear = lambda value: value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4

with tempfile.TemporaryDirectory(prefix='mme-grgd-tev-') as temp:
    temp = Path(temp)
    directory = temp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrGd.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    group = read(directory / 'models/group-001/group.json')
    nodes = {node['id']: node for node in group['nodes']}
    jobj = next(node for node in group['nodes'] if node['kind'] == 'jobj' and node['index'] == 4)
    dobj = next(node for node in group['nodes']
                if node['kind'] == 'dobj' and node['index'] == 0 and node['ownerId'] == jobj['id'])
    pobj = next(node for node in group['nodes'] if node.get('ownerId') == dobj['id'])
    entry = next(item for item in stage['modelPreviews'] if item['id'] == pobj['id'])
    tev = entry['preview']['texture']['tev']
    assert tev['colorOperation'] == 0 and tev['colorClamp']
    assert [tev[name] for name in ('colorA', 'colorB', 'colorC', 'colorD')] == [0x85, 0x80, 8, 15]
    assert [round(value * 255) for value in tev['konst'][:3]] == [25, 25, 25]
    assert [round(value * 255) for value in tev['tev0'][:3]] == [25, 25, 25]

    scene.import_session(bpy.context, directory)
    obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == pobj['id'])
    assert obj.active_material.node_tree.nodes['Stage Custom TEV']

    # The animated sunbeams use white vertex RGB and a magenta MOBJ diffuse
    # value that HSD ignores. Material-animation refresh must not tint them.
    animated_group = read(directory / 'models/group-004/group.json')
    sunbeam_jobj = next(node for node in animated_group['nodes']
                         if node['kind'] == 'jobj' and node['index'] == 43)
    sunbeam_dobj = next(node for node in animated_group['nodes']
                         if node['kind'] == 'dobj' and node['index'] == 0
                         and node['ownerId'] == sunbeam_jobj['id'])
    sunbeam_pobj = next(node for node in animated_group['nodes']
                         if node.get('ownerId') == sunbeam_dobj['id'])
    sunbeam_entry = next(item for item in stage['modelPreviews']
                         if item['id'] == sunbeam_pobj['id'])
    assert sunbeam_entry['preview']['useVertexColor']
    assert sunbeam_entry['preview']['color'][:3] == [1, 0, 1]
    sunbeam = next(obj for obj in bpy.context.scene.objects
                   if obj.get('mme_id') == sunbeam_pobj['id'])
    bpy.context.scene.frame_set(60)
    bpy.context.view_layer.update()
    sunbeam_nodes = sunbeam.active_material.node_tree.nodes
    tint = sunbeam_nodes['Stage Diffuse Tint']
    assert tuple(tint.inputs[1].default_value[:3]) == (1, 1, 1)
    assert sunbeam_nodes['Stage Color Modulation'].inputs[2].links[0].from_node \
        == sunbeam_nodes['Stage Vertex Color']

    # Render the extracted combiner against a known white texel. The custom
    # stage must replace white RGB with the source 25/255 constant.
    header = bytearray(18)
    header[2] = 2
    header[12] = header[14] = 1
    header[16] = 32
    header[17] = 0x28
    (temp / 'white.tga').write_bytes(header + bytes([255, 255, 255, 255]))
    texture = dict(file='white.tga', width=1, height=1, wrapS=0, wrapT=0,
                   repeatS=1, repeatT=1, scale=[1, 1, 1], rotation=[0, 0, 0],
                   translation=[0, 0, 0], colorOperation=3, colorBlend=1,
                   texCoord=0, lightmapFlags=0x10, coordinateType=0, tev=tev)
    material = bpy.data.materials.new('Dream Land custom TEV test')
    surface.configure_preview(material, dict(color=[.1, .1, .1, 1], texture=texture,
                              textures=[texture]), temp,
                              dict(baselineFiles=[dict(file='white.tga')]))
    render_scene = bpy.data.scenes.new('Dream Land custom TEV render')
    bpy.context.window.scene = render_scene
    mesh = bpy.data.meshes.new('TEV plane')
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for item in uv.data:
        item.uv = (.5, .5)
    plane = bpy.data.objects.new('TEV plane', mesh)
    render_scene.collection.objects.link(plane)
    mesh.materials.append(material)
    camera = bpy.data.objects.new('TEV camera', bpy.data.cameras.new('TEV camera'))
    render_scene.collection.objects.link(camera)
    camera.location = (0, 0, 10)
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 2
    render_scene.camera = camera
    render_scene.render.engine = 'CYCLES'
    render_scene.cycles.device = 'CPU'
    render_scene.cycles.samples = 1
    render_scene.render.resolution_x = render_scene.render.resolution_y = 16
    render_scene.render.resolution_percentage = 100
    render_scene.render.image_settings.file_format = 'OPEN_EXR'
    render_scene.render.filepath = str(temp / 'tev.exr')
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(render_scene.render.filepath)
    pixel = list(image.pixels)[(8 * 16 + 8) * 4:(8 * 16 + 8) * 4 + 3]
    expected = linear(25 / 255)
    assert all(abs(value - expected) < .001 for value in pixel), (pixel, expected)

print('GRGD MATERIAL PREVIEW PASS: dark custom TEV overlay and white vertex-color animated sunbeams')
