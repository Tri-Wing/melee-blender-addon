"""Real GrSt vertex color/alpha, corner seams, panel fill, save/reload and DAT roundtrip."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
with tempfile.TemporaryDirectory(prefix='mme-color-export-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrSt.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    info = next(i for i in modeling.targets(s) if (i['groupIndex'], i['jobjIndex'], i['dobjIndex']) == (3, 4, 3))
    obj = modeling.target_object(s, info)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    source = read(tmp / 'session' / info['file'])
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrSt.dat').read_bytes()
    material = obj.active_material
    assert material.mme_use_vertex_color
    from melee_map_editor import material_properties
    assert material_properties.definition(material)['useVertexColor']
    assert material.mme_use_vertex_color
    definition = material_properties.definition(material)
    material.mme_use_vertex_color = False
    material.mme_alpha_source = 'MATERIAL'
    material.mme_transparency = 'ALPHA'
    material.mme_diffuse_lighting = True
    material.mme_no_depth_write = True
    assert material.node_tree.nodes['Stage Surface'].type == 'EMISSION'
    assert material.node_tree.nodes['Stage Diffuse Lighting'].blend_type == 'MULTIPLY'
    assert material.node_tree.nodes.get('Stage Vertex Color') is None
    mode_edit = material_properties.edits(s, stage)
    expected_flags = definition['renderFlags'] | (1 << 2) | (1 << 29)
    assert mode_edit['materials'] == [{'id': info['id'], 'alphaSource': 1, 'transparencyMode': 1,
                                       'renderFlags': expected_flags, 'useVertexColor': False}]
    scene.apply(s, CLI, 'dotnet', tmp / 'material-mode.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'material-mode.dat', '--session', tmp / 'material-mode')
    mode_stage = read(tmp / 'material-mode/stage.json')
    mode_info = next(i for i in mode_stage['editableMeshes'] if
                     (i['groupIndex'], i['jobjIndex'], i['dobjIndex']) == (3, 4, 3))
    mode_definition = next(i for i in mode_stage['editableMaterialProperties'] if i['id'] == mode_info['id'])
    assert not mode_definition['useVertexColor']
    assert mode_definition['alphaSource'] == 1
    assert mode_definition['canEditDiffuse'] and mode_definition['canEditAlpha']
    assert mode_definition['transparencyMode'] == 1
    assert mode_definition['renderFlags'] & (1 << 30)
    assert mode_definition['renderFlags'] & (1 << 29)
    material.mme_use_vertex_color = True
    material.mme_alpha_source = next(name for name, value in material_properties.ALPHA_SOURCES.items()
                                     if value == definition['alphaSource'])
    material.mme_transparency = next(name for name, value in material_properties.TRANSPARENCY.items()
                                     if value == definition['transparencyMode'])
    for name, bit in material_properties.RENDER_FLAGS:
        setattr(material, name, bool(definition['renderFlags'] & (1 << bit)))
    nodes = material.node_tree.nodes
    assert nodes['Stage Surface'].type == 'EMISSION'
    assert bool(nodes.get('Stage Diffuse Lighting')) == bool(definition['renderFlags'] & 12)
    assert bool(nodes.get('Stage Specular Add')) == bool(definition['renderFlags'] & 8)
    assert material.node_tree.nodes.get('Stage Vertex Color')
    layer = obj.data.color_attributes['Stage Color 0']
    # Blender's native vertex-paint/color-attribute path changes individual corners.
    for i in obj.data.polygons[0].loop_indices:
        value = list(layer.data[i].color)
        value[3] = .25
        layer.data[i].color = value
    assert all(abs(layer.data[i].color[3] - .25) < 1e-6 for i in obj.data.polygons[0].loop_indices)
    # A seam: two corners referencing the same vertex retain independent RGBA.
    layer = obj.data.color_attributes['Stage Color 0']
    shared = next(i for i in range(len(obj.data.vertices)) if sum(l.vertex_index == i for l in obj.data.loops) > 1)
    loops = [l.index for l in obj.data.loops if l.vertex_index == shared]
    layer.data[loops[0]].color = (.1, .3, .5, .7)
    expected = [list(layer.data[i].color) for face in obj.data.polygons for i in face.loop_indices]
    obj.data.vertices[shared].co.z += 2
    edit = modeling.edits(s, stage)
    assert len(edit['meshes']) == 1 and 'colors0' in edit['meshes'][0]
    scene.apply(s, CLI, 'dotnet', tmp / 'painted.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'painted.dat', '--session', tmp / 'painted')
    out_stage = read(tmp / 'painted/stage.json')
    out_info = next(i for i in out_stage['editableMeshes'] if (i['groupIndex'], i['jobjIndex'], i['dobjIndex']) == (3, 4, 3))
    result = read(tmp / 'painted' / out_info['file'])
    assert result['triangleIndices'] == list(range(len(expected)))
    for value, actual in zip(expected, result['colors0']):
        assert all(abs(v - actual[c]) <= .5001 / 255 for v, c in zip(value, ('r', 'g', 'b', 'a')))
    for key in ('normals', 'texCoords0', 'colors1'):
        if source.get(key) is not None:
            assert result[key] == [source[key][i] for i in source['triangleIndices']], key
    # Original material and texture definitions remain intact.
    out_stage = read(tmp / 'painted/stage.json')
    before = next(p for p in stage['modelPreviews'] if p['id'] == info['id'])
    after = next(p for p in out_stage['modelPreviews'] if p['id'] == out_info['id'])
    assert before['preview'] == after['preview']
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'painted.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'painted.blend'))
    scene.apply(bpy.context.scene, CLI, 'dotnet', tmp / 'reopened.dat')
    assert (tmp / 'reopened.dat').read_bytes() == (tmp / 'painted.dat').read_bytes()
print('VERTEX COLOR EXPORT PASS')
