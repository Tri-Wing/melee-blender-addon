"""Corner color channels, preview wiring, real-stage import and export isolation."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, surface, modeling
from melee_map_editor.protocol import read, run
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
mesh = bpy.data.meshes.new('Color Fixture')
mesh.from_pydata([(0,0,0), (1,0,0), (0,1,0), (0,0,0)], [], [(0,1,2), (3,2,1)])
values = [{'r': r, 'g': g, 'b': b, 'a': a} for r,g,b,a in [(1,0,0,1),(0,1,0,.5),(0,0,1,1),(1,1,0,0)]]
layers = surface.import_colors(mesh, {'colors0': values, 'colors1': list(reversed(values))})
assert layers == ['Stage Color 0', 'Stage Color 1']
for channel, source in enumerate((values, list(reversed(values)))):
    layer = mesh.color_attributes[layers[channel]]
    assert layer.domain == 'CORNER'
    for loop in mesh.loops:
        assert tuple(layer.data[loop.index].color) == tuple(source[loop.vertex_index][c] for c in ('r','g','b','a'))
material = bpy.data.materials.new('Color fixture')
surface.configure_color_preview(material, layers[0])
assert material.node_tree.nodes['Stage Vertex Color'].layer_name == layers[0]
assert material.node_tree.nodes['Stage Color Modulation'].blend_type == 'MULTIPLY'
with tempfile.TemporaryDirectory(prefix='mme-colors-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    count = 0
    for group in stage['modelGroups']:
        path = tmp / 'session' / group['file']
        for filename in read(path)['meshes']:
            source = read(path.parent / filename)
            obj = next(o for o in s.objects if o.get('mme_id') == source['id'])
            for channel in range(2):
                colors = source.get(f'colors{channel}')
                if colors is None:
                    continue
                count += 1
                layer = obj.data.color_attributes[f'Stage Color {channel}']
                for loop in obj.data.loops:
                    expected = colors[loop.vertex_index]
                    assert all(abs(a-expected[c]) < 1e-6 for a,c in zip(layer.data[loop.index].color, ('r','g','b','a')))
                assert obj.active_material.node_tree.nodes.get('Stage Vertex Color')
    assert count > 0
    print(f'Imported {count} stage color channels')
    # Painting is preview-only; no-edit export must still retain source bytes.
    colored = next(o for o in s.objects if o.type == 'MESH' and o.data.color_attributes.get('Stage Color 0'))
    colored.data.color_attributes['Stage Color 0'].data[0].color = (.1, .2, .3, .4)
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    for info in modeling.targets(s):
        modeling.target_object(s, info).data.vertices[0].co.z += 1
    scene.apply(s, CLI, 'dotnet', tmp / 'moved.dat')
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'colors.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'colors.blend'))
    scene.apply(bpy.context.scene, CLI, 'dotnet', tmp / 'reopened.dat')
    assert (tmp / 'moved.dat').read_bytes() == (tmp / 'reopened.dat').read_bytes()
print('VERTEX COLORS PASS')
