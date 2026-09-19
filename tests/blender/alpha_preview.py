"""Render alpha sources, cutouts, texture operations and additive black backgrounds."""
from pathlib import Path
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import surface

base = dict(material=1, vertex=False, multiplyMaterial=False, textureOperation=0,
            textureBlend=.5, blendMode=1, sourceFactor=4, destinationFactor=5,
            compare0=7, reference0=0, operation=0, compare1=7, reference1=0)
cases = [
    ('opaque black', dict(blendMode=0), (0,0,0), None, None, (0,0,0)),
    ('material alpha', dict(material=.5), (1,0,0), None, None, (.5,0,.5)),
    ('vertex alpha', dict(vertex=True), (1,0,0), .25, None, (.25,0,.75)),
    ('combined alpha', dict(vertex=True, multiplyMaterial=True, material=.5), (1,0,0), .5, None, (.25,0,.75)),
    ('texture replace', dict(textureOperation=4), (1,0,0), None, .25, (.25,0,.75)),
    ('texture modulate', dict(textureOperation=3, material=.5), (1,0,0), None, .5, (.25,0,.75)),
    ('texture blend', dict(textureOperation=2, material=0), (1,0,0), None, 1, (.5,0,.5)),
    ('texture mask', dict(textureOperation=1, material=0), (1,0,0), None, .5, (.25,0,.75)),
    ('texture add', dict(textureOperation=6, material=.25), (1,0,0), None, .25, (.5,0,.5)),
    ('texture subtract', dict(textureOperation=7, material=.75), (1,0,0), None, .25, (.5,0,.5)),
    ('texture pass', dict(textureOperation=5, material=.5), (1,0,0), None, 0, (.5,0,.5)),
    ('black intensity alpha', dict(textureOperation=4), (0,0,0), None, 0, (0,0,1)),
    ('cutout discard', dict(blendMode=0, material=.25, compare0=4, reference0=128), (1,0,0), None, None, (0,0,1)),
    ('cutout accept', dict(blendMode=0, material=.75, compare0=4, reference0=128), (1,0,0), None, None, (1,0,0)),
    ('cutout or', dict(blendMode=0, material=.25, compare0=4, reference0=128, operation=1), (1,0,0), None, None, (1,0,0)),
    ('additive black', dict(sourceFactor=1, destinationFactor=1), (0,0,0), None, None, (0,0,1)),
    ('additive red', dict(sourceFactor=1, destinationFactor=1), (1,0,0), None, None, (1,0,1)),
    ('additive alpha', dict(destinationFactor=1, material=.5), (1,0,0), None, None, (.5,0,1)),
]
s = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def plane(name, x0, x1, z, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x0,-.5,z),(x1,-.5,z),(x1,.5,z),(x0,.5,z)], [], [(0,1,2,3)])
    obj = bpy.data.objects.new(name, mesh)
    s.collection.objects.link(obj)
    mesh.materials.append(material)
    return obj

bg = bpy.data.materials.new('Blue background')
surface.configure_preview(bg, {'color': [0,0,1,1]}, None, {})
plane('background', 0, len(cases), -1, bg)
for i, (name, changes, rgb, vertex, tex_alpha, _) in enumerate(cases):
    settings = base | changes
    material = bpy.data.materials.new(name)
    surface.configure_preview(material, {'color': [*rgb,1], 'alpha': settings}, None, {})
    obj = plane(name, i, i+1, 0, material)
    if vertex is not None:
        colors = [dict(r=1,g=1,b=1,a=vertex)] * 4
        surface.import_colors(obj.data, {'colors0': colors})
        surface.configure_color_preview(material, 'Stage Color 0')
    if tex_alpha is not None:
        image = bpy.data.images.new(name, width=1, height=1, alpha=True, float_buffer=True)
        image.pixels[:] = (1,1,1,tex_alpha)
        image.pack()
        texture = material.node_tree.nodes.new('ShaderNodeTexImage')
        texture.name = 'Stage Texture'
        texture.image = image
        surface.configure_alpha_preview(material, settings)
    assert material.surface_render_method == 'DITHERED'

camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
s.collection.objects.link(camera)
camera.location = (len(cases)/2, 0, 10)
camera.data.type = 'ORTHO'
camera.data.ortho_scale = len(cases)
s.camera = camera
s.render.engine = 'CYCLES'
s.cycles.device = 'CPU'
s.cycles.samples = 8
s.render.resolution_x = len(cases)*32
s.render.resolution_y = 32
s.render.resolution_percentage = 100
s.render.image_settings.file_format = 'OPEN_EXR'
s.render.image_settings.color_mode = 'RGBA'
with tempfile.TemporaryDirectory(prefix='mme-alpha-render-') as tmp:
    path = str(Path(tmp) / 'alpha.exr')
    s.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path, check_existing=False)
    pixels = list(image.pixels)
    for i, (name, _, _, _, _, expected) in enumerate(cases):
        index = (16*s.render.resolution_x + i*32 + 16)*4
        actual = pixels[index:index+3]
        assert all(abs(a-b) < .025 for a,b in zip(actual, expected)), (name, actual, expected)
    # Saving must retain node settings and transparency setup.
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tmp) / 'alpha.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(tmp) / 'alpha.blend'))
    assert bpy.data.materials['additive black'].node_tree.nodes['Stage Alpha Surface'].type == 'ADD_SHADER'
print(f'ALPHA PREVIEW PASS: {len(cases)} rendered cases')
