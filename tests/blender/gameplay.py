"""Gameplay markers and boundary guides round-trip through Blender and the DAT writer."""
from pathlib import Path
import os
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor as addon
from melee_map_editor import gameplay, scene
from melee_map_editor.protocol import StageError, read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))


def rejects(action, message):
    try:
        action()
    except StageError as exc:
        assert message.lower() in str(exc).lower(), (message, str(exc))
    else:
        raise AssertionError('Expected rejection: ' + message)


bpy.ops.preferences.addon_enable(module='melee_map_editor')
with tempfile.TemporaryDirectory(prefix='mme-gameplay-blender-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    guides = gameplay.objects(bpy.context.scene)
    points = [obj for obj in guides if obj.get('mme_role') == 'gameplay-point']
    bounds = [obj for obj in guides if obj.get('mme_role') == 'gameplay-bounds']
    assert len(guides) == 18
    assert len(points) == 16
    assert {obj.get('mme_gameplay_player') for obj in points
            if obj.get('mme_gameplay_kind') == 'player-spawn'} == {1, 2, 3, 4}
    assert {obj.get('mme_gameplay_kind') for obj in bounds} == {
        'camera-boundary', 'blast-zone'}
    assert all(obj.get('mme_gameplay_editable') for obj in guides)
    projected = gameplay.project_to_plane((0, -10, 0), (1, 1, 2), 0)
    assert tuple(projected) == (10, 0, 20)
    rejects(lambda: gameplay.project_to_plane((0, 0, 0), (1, 0, 0), 0),
            'more face-on')
    # Managed names are user labels, not identities or serialized metadata.
    for index, obj in enumerate(guides):
        obj.name = f'Renamed Gameplay Guide {index}'
    assert gameplay.edits(bpy.context.scene, stage) is None
    scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'unchanged.dat')
    assert (temporary / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()

    spawn = next(obj for obj in points
                 if obj.get('mme_gameplay_kind') == 'player-spawn'
                 and obj.get('mme_gameplay_player') == 1)
    blast = next(obj for obj in bounds
                 if obj.get('mme_gameplay_kind') == 'blast-zone')
    assert len(spawn.data.polygons) == 0
    assert len(spawn.data.edges) == 4
    assert len(spawn.data.materials) == 0
    spawn.location.x += 2.5
    spawn.location.z += 1.25
    blast.scale.x *= 1.05
    blast.scale.z *= 1.05
    item = next(obj for obj in points
                if obj.get('mme_gameplay_kind') == 'item-spawn')
    item_set = item['mme_group_index']
    deleted_id = item['mme_id']
    deleted_type = item['mme_gameplay_type_id']
    bpy.ops.object.select_all(action='DESELECT')
    item.select_set(True)
    bpy.context.view_layer.objects.active = item
    assert bpy.ops.object.delete() == {'FINISHED'}
    first_type = gameplay.item_spawn_slot(bpy.context.scene, item_set)
    added_a = gameplay.add_item_spawn(
        bpy.context.scene, stage, item_set, first_type, (12.5, 0, 34.25))
    second_type = gameplay.item_spawn_slot(bpy.context.scene, item_set)
    added_b = gameplay.add_item_spawn(
        bpy.context.scene, stage, item_set, second_type, (-20, 0, 18))
    cancelled_type = gameplay.item_spawn_slot(bpy.context.scene, item_set)
    cancelled = gameplay.add_item_spawn(
        bpy.context.scene, stage, item_set, cancelled_type, (1, 0, 2))
    assert bpy.context.active_object == cancelled
    assert bpy.ops.object.delete() == {'FINISHED'}
    payload = gameplay.edits(bpy.context.scene, stage)
    assert len(payload['points']) == 3
    assert len(payload['additions']) == 2
    assert payload['deletions'] == [deleted_id]
    assert [entry['typeId'] for entry in payload['additions']] == [deleted_type, second_type]
    assert scene.prepare(bpy.context.scene)[1] is None
    output = temporary / 'edited.dat'
    scene.apply(bpy.context.scene, CLI, 'dotnet', output)
    run(CLI, 'dotnet', 'extract', output, '--session', temporary / 'edited-session')
    edited = read(temporary / 'edited-session/stage.json')['gameplay']
    source = {point['id']: point for source_set in stage['gameplay']['sets']
              for point in source_set['points']}
    actual = {point['id']: point for source_set in edited['sets']
              for point in source_set['points']}
    spawn_id = spawn['mme_id']
    actual_spawn = next(point for source_set in edited['sets']
                        for point in source_set['points']
                        if source_set['index'] == spawn['mme_group_index']
                        and point['typeId'] == spawn['mme_gameplay_type_id'])
    assert actual_spawn['position']['x'] == source[spawn_id]['position']['x'] + 2.5
    assert actual_spawn['position']['y'] == source[spawn_id]['position']['y'] + 1.25
    actual_items = [point for source_set in edited['sets']
                    for point in source_set['points'] if point['kind'] == 'item-spawn']
    assert len(actual_items) == len([point for source_set in stage['gameplay']['sets']
                                    for point in source_set['points']
                                    if point['kind'] == 'item-spawn']) + 1
    expected_added = {deleted_type: (12.5, 34.25), second_type: (-20, 18)}
    for type_id, (x, y) in expected_added.items():
        point = next(point for point in actual_items if point['typeId'] == type_id)
        assert (point['position']['x'], point['position']['y'], point['position']['z']) == (x, y, 0)

    original_rotation = spawn.rotation_euler.x
    spawn.rotation_euler.x = 0.25
    rejects(lambda: gameplay.edits(bpy.context.scene, stage), 'cannot be rotated')
    spawn.rotation_euler.x = original_rotation

print('BLENDER_GAMEPLAY_OK: line guides, movement, native-delete item spawns, guard, reload')
