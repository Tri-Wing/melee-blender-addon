"""LOBJ collections and the selected GrNLa descriptor lighting graph."""
import os
import math
from pathlib import Path
import sys
import tempfile
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, surface
from melee_map_editor.protocol import read, run, StageError

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4

with tempfile.TemporaryDirectory(prefix='mme-lighting-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    assert stage['lighting']['previewSetId'] == 'group-003'
    assert len(stage['lighting']['lightSets']) == 11
    selected = next(s for s in stage['lighting']['lightSets'] if s['id'] == 'group-003')
    assert selected['animated'] and len(selected['lights']) == 3
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    sets = [c for c in bpy.data.collections if c.get('mme_role') == 'light-set'
            and c.get('mme_session_id') == s.mme_session_id]
    assert len(sets) == 11
    assert sum(len(c.objects) for c in sets) == 26
    preview_collection = next(c for c in sets if c.get('mme_id') == 'lights-group-003')
    assert not preview_collection.hide_viewport
    assert all(c.hide_viewport for c in sets if c != preview_collection)
    preview_objects = list(preview_collection.objects)
    assert [o.get('mme_light_type') for o in preview_objects] == ['ambient', 'infinite', 'infinite']
    assert all(o.get('mme_light_animated') for o in preview_objects)
    assert preview_objects[0].type == 'EMPTY'
    assert all(o.type == 'LIGHT' and o.data.type == 'SUN' for o in preview_objects[1:])

    info = next(i for i in stage['editableMeshes']
                if (i['groupIndex'], i['jobjIndex'], i['dobjIndex']) == (3, 4, 0))
    material = modeling.target_object(s, info).active_material
    nodes = material.node_tree.nodes
    assert nodes['Stage Surface'].type == 'EMISSION'
    assert not any(node.type in {'BSDF_DIFFUSE', 'BSDF_GLOSSY', 'BSDF_PRINCIPLED'} for node in nodes)
    ambient_source = selected['lights'][0]
    ambient_obj = next(o for o in preview_objects if o.get('mme_id') == ambient_source['id'])
    ambient_color = nodes[f"Stage Light Color {ambient_source['id']}"]
    assert all(abs(ambient_color.outputs[0].default_value[i] - linear(76 / 255)) < 1e-7
               for i in range(3))
    diffuse_dots = [node for node in nodes if node.name.startswith('Stage Diffuse N dot L')]
    specular_dots = [node for node in nodes if node.name.startswith('Stage Specular N dot H')]
    assert len(diffuse_dots) == len(specular_dots) == 2
    first_source = selected['lights'][1]
    first_obj = next(o for o in preview_objects if o.get('mme_id') == first_source['id'])
    first = first_source['position']
    expected = -Vector((first['x'], -first['z'], first['y'])).normalized()
    direction_node = nodes[f"Stage Light Direction {first_source['id']}"]
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    actual = Vector(tuple(socket.default_value for socket in direction_node.inputs[:3]))
    assert (actual - expected).length < 1e-6
    original_direction = actual.copy()
    first_obj.rotation_euler.y += .4
    first_obj.data.color = (.1, .2, .3)
    first_obj.data.energy = .25
    ambient_obj['mme_light_intensity'] = .5
    ambient_obj.update_tag()
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    actual = Vector(tuple(socket.default_value for socket in direction_node.inputs[:3]))
    assert (actual - original_direction).length > .1
    color_node = nodes[f"Stage Light Color {first_source['id']}"]
    assert all(abs(color_node.outputs[0].default_value[i] - value) < 1e-6
               for i, value in enumerate((.1, .2, .3)))
    assert abs(nodes[f"Stage Light Strength {first_source['id']}"].outputs[0].default_value - .25) < 1e-6
    ambient_strength = nodes[f"Stage Light Strength {ambient_source['id']}"].outputs[0].default_value
    assert abs(ambient_strength - .5) < 1e-6, ambient_strength

    # Render a white test surface with only ambient LOBJ energy, then disable it.
    # This catches drivers that update socket values but do not affect evaluation.
    controls = {obj.get('mme_id'): obj for obj in preview_objects}
    for obj in preview_objects[1:]:
        obj.data.energy = 0
    ambient_obj['mme_light_intensity'] = 1
    ambient_obj.update_tag()
    check = bpy.data.scenes.new('LOBJ render')
    bpy.context.window.scene = check
    mat = bpy.data.materials.new('LOBJ render material')
    surface.configure_preview(mat, {'color': [1, 1, 1, 1], 'diffuseLighting': True},
                              None, stage, controls)
    mesh = bpy.data.meshes.new('LOBJ render plane')
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    plane = bpy.data.objects.new(mesh.name, mesh)
    check.collection.objects.link(plane)
    mesh.materials.append(mat)
    camera = bpy.data.objects.new('LOBJ render camera', bpy.data.cameras.new('LOBJ render camera'))
    check.collection.objects.link(camera)
    camera.location = (0, 0, 5)
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 2
    check.camera = camera
    check.render.engine = 'CYCLES'
    check.cycles.device = 'CPU'
    check.cycles.samples = 1
    check.render.resolution_x = check.render.resolution_y = 8
    check.render.resolution_percentage = 100
    check.render.image_settings.file_format = 'OPEN_EXR'
    check.view_settings.view_transform = 'Standard'
    check.view_settings.look = 'None'
    levels = []
    for value, name in ((1, 'on'), (0, 'off')):
        ambient_obj['mme_light_intensity'] = value
        ambient_obj.update_tag()
        bpy.context.view_layer.update()
        check.render.filepath = str(tmp / f'light-{name}.exr')
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(check.render.filepath, check_existing=False)
        levels.append(sum(image.pixels[(4 * 8 + 4) * 4:(4 * 8 + 4) * 4 + 3]))
    assert levels[0] > levels[1] + .05, levels
    ambient_obj['mme_light_intensity'] = 0
    ambient_obj.update_tag()
    first_obj.data.color = (1, 1, 1)
    first_obj.data.energy = 1
    direction_levels = []
    for rotation, name in (((0, 0, 0), 'down'), ((math.pi, 0, 0), 'up')):
        first_obj.rotation_euler = rotation
        bpy.context.view_layer.update()
        check.render.filepath = str(tmp / f'light-rays-{name}.exr')
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(check.render.filepath, check_existing=False)
        direction_levels.append(sum(image.pixels[(4 * 8 + 4) * 4:(4 * 8 + 4) * 4 + 3]))
    assert direction_levels[0] > direction_levels[1] + .5, direction_levels
    bpy.context.window.scene = s
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()

print('LIGHTING PASS: imported LOBJ transforms, colors and intensities drive the preview')
