"""Read-only HSD material and texture animation playback."""
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import animations, scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)


def import_stage(temp, filename):
    directory = temp / filename.removesuffix('.dat')
    run(CLI, 'dotnet', 'extract', CORPUS / filename, '--session', directory)
    scene.import_session(bpy.context, directory)
    return directory


with tempfile.TemporaryDirectory(prefix='mme-material-animation-') as raw:
    temp = Path(raw)
    directory = import_stage(temp, 'GrGb.dat')
    group = read(directory / 'models/group-003/group.json')
    animation = group['materialAnimations'][0]
    target = animation['materials'][0]
    assert any(track['channel'] == 'translation.x'
               for texture in target['textures'] for track in texture['tracks'])
    obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == target['materialId'])
    material = obj.active_material
    armature = next(obj for obj in bpy.context.scene.objects
                    if obj.get('mme_role') == 'jobj-armature' and obj.get('mme_group_index') == 3)
    assert animations.active_action(armature)['mme_animation_slot'] == 0
    offset = material.node_tree.nodes['Stage Texture Offset U'].inputs[1]
    bpy.context.scene.frame_set(1)
    start = offset.default_value
    bpy.context.scene.frame_set(600)
    middle = offset.default_value
    bpy.context.scene.frame_set(1200)
    wrapped = offset.default_value
    assert abs(start - middle) > .01
    assert abs(start - wrapped) < 1e-6
    scene.apply(bpy.context.scene, CLI, 'dotnet', temp / 'grgb-noop.dat')
    assert (temp / 'grgb-noop.dat').read_bytes() == (CORPUS / 'GrGb.dat').read_bytes()

    fresh = bpy.data.scenes.new('Material alpha animation')
    bpy.context.window.scene = fresh
    directory = import_stage(temp, 'GrNLa.dat')
    group = read(directory / 'models/group-003/group.json')
    animation = group['materialAnimations'][0]
    target = next(material for material in animation['materials']
                  if any(track['channel'] == 'alpha' for track in material['tracks']))
    obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == target['materialId'])
    alpha = obj.active_material.node_tree.nodes['Stage Material Alpha'].outputs[0]
    bpy.context.scene.frame_set(1)
    opaque = alpha.default_value
    bpy.context.scene.frame_set(41)
    transparent = alpha.default_value
    assert opaque > .99 and transparent < .01, (opaque, transparent)

    group = read(directory / 'models/group-006/group.json')
    animation = next(item for item in group['materialAnimations'] if item['slot'] == 6)
    target = next(material for material in animation['materials']
                  if any(track['channel'] == 'diffuse.r' for track in material['tracks']))
    obj = next(obj for obj in bpy.context.scene.objects if obj.get('mme_id') == target['materialId'])
    tint = obj.active_material.node_tree.nodes['Stage Diffuse Tint']
    assert tint.get('mme_texture_index') == 0
    group_armature = next(obj for obj in bpy.context.scene.objects
                          if obj.get('mme_role') == 'jobj-armature' and obj.get('mme_group_index') == 6)
    group_armature.animation_data.action = next(
        action for action in animations.actions(group_armature)
        if action.get('mme_animation_slot') == 6)
    bpy.context.scene.frame_set(1)
    bright = tint.inputs[1].default_value[0]
    bpy.context.scene.frame_set(100)
    dark = tint.inputs[1].default_value[0]
    assert bright > .2 and dark < 1e-6, (
        bright, dark, animations.active_action(group_armature).get('mme_animation_slot'),
        tint.get('mme_texture_index'), tint.inputs[1].is_linked)
    group_armature.animation_data.action = next(
        action for action in animations.actions(group_armature)
        if action.get('mme_animation_slot') == 0)
    animations.apply(bpy.context.scene)
    restored = tint.inputs[1].default_value[0]
    assert abs(restored - bright) < 1e-6, (bright, restored)

print('MATERIAL ANIMATIONS PASS: slots, looping texture transforms, diffuse, alpha, slot reset, no-op preservation')
