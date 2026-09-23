"""Animated attribute sharing, graph rebuilds, reload, and DAT preservation."""
import json
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor as addon
from melee_map_editor import animations, animation_values, scene, surface
from melee_map_editor.protocol import read, run, StageError

addon.register()
cli = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
source = ROOT / 'example_assets/GrNLa.dat'
with tempfile.TemporaryDirectory(prefix='mme-animation-values-') as raw:
    directory = Path(raw)
    run(cli, 'dotnet', 'extract', source, '--session', directory / 'session')
    scene.import_session(bpy.context, directory / 'session')
    current = bpy.context.scene
    current.eevee.use_taa_reprojection = False
    payload = read(directory / 'session/models/group-003/group.json')
    target = next(target for target in payload['materialAnimations'][0]['materials']
                  if any(track['channel'] == 'alpha' for track in target['tracks']))
    obj = next(obj for obj in current.objects if obj.get('mme_id') == target['materialId'])
    material = obj.active_material
    alpha = material.node_tree.nodes['Stage Material Alpha'].outputs[0]
    current.frame_set(1)
    assert animation_values.value(alpha, obj) > .99
    current.frame_set(41)
    assert animation_values.value(alpha, obj) < .01
    assert animation_values.bindings(material)

    # An additional owner and copied material in a second slot must both receive
    # current values, including controls unchanged since the preceding frame.
    extra = bpy.data.objects.new('Additional preview owner', obj.data.copy())
    current.collection.objects.link(extra)
    copied = material.copy()
    extra.data.materials.clear()
    extra.data.materials.append(material)
    extra.data.materials.append(copied)
    current.frame_set(42)
    copied_alpha = copied.node_tree.nodes['Stage Material Alpha'].outputs[0]
    assert animation_values.value(alpha, extra) < .01
    assert animation_values.value(copied_alpha, extra) < .01
    original_names = {n.attribute_name for n in animation_values.bindings(material).values()}
    copied_names = {n.attribute_name for n in animation_values.bindings(copied).values()}
    assert original_names.isdisjoint(copied_names)
    current.frame_set(1)
    assert animation_values.value(alpha, extra) > .99
    assert animation_values.value(copied_alpha, extra) > .99
    bpy.data.objects.remove(extra, do_unlink=True)
    bpy.data.materials.remove(copied)

    # Rebuilding alpha must invalidate attribute/socket bindings and reapply
    # animation at the same frame, without leaving stale pointers or values.
    current.frame_set(41)
    surface.configure_alpha_preview(material, json.loads(material['mme_alpha_preview']))
    animations.apply(current)
    alpha = material.node_tree.nodes['Stage Material Alpha'].outputs[0]
    assert animation_values.value(alpha, obj) < .01
    current.frame_set(1)
    assert animation_values.value(alpha, obj) > .99
    current.frame_set(41)
    # Switching temporal reprojection restores the socket path at the same
    # frame. Disabling it again can return to attributes on the next change.
    current.eevee.use_taa_reprojection = True
    animations.apply(current)
    assert not animation_values.bindings(material)
    assert animation_values.value(alpha, obj) < .01
    current.eevee.use_taa_reprojection = False
    animations.apply(current)
    current.frame_set(1)
    assert animation_values.bindings(material)
    current.frame_set(41)
    name = obj.name
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / 'animated.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(directory / 'animated.blend'))
    current = bpy.context.scene
    obj = current.objects[name]
    alpha = obj.active_material.node_tree.nodes['Stage Material Alpha'].outputs[0]
    assert animation_values.value(alpha, obj) < .01
    current.frame_set(1)
    assert animation_values.value(alpha, obj) > .99
    scene.apply(current, cli, 'dotnet', directory / 'no-edit.dat')
    assert (directory / 'no-edit.dat').read_bytes() == source.read_bytes()
    original_id = obj['mme_id']
    obj['mme_id'] = 'invalid identity'
    try:
        scene.prepare(current)
    except StageError:
        pass
    else:
        raise AssertionError('Preview property exclusions must not weaken identity validation')
    obj['mme_id'] = original_id
addon.unregister()
print('ANIMATION VALUE ATTRIBUTES PASS')
