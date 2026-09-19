"""Point and spot Blender controls round-trip through static LOBJ export."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4

with tempfile.TemporaryDirectory(prefix='mme-light-types-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNSr.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    source_by_type = {}
    for light_set in stage['lighting']['lightSets']:
        for light in light_set['lights']:
            source_by_type.setdefault(light['type'], light)
    assert {'point', 'spot'} <= set(source_by_type)
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    objects = {obj.get('mme_id'): obj for obj in s.objects if obj.get('mme_role') == 'light'}
    point_source, spot_source = source_by_type['point'], source_by_type['spot']
    point, spot = objects[point_source['id']], objects[spot_source['id']]

    point_game = Vector((11, 22, 33))
    point.location = (point_game.x, -point_game.z, point_game.y)
    point.data.color = tuple(linear(value / 255) for value in (25, 100, 225))
    point['mme_light_enabled'] = False

    spot_game = Vector((-4, 5, 6))
    interest_game = Vector((7, 8, 9))
    spot.location = (spot_game.x, -spot_game.z, spot_game.y)
    interest_blender = Vector((interest_game.x, -interest_game.z, interest_game.y))
    direction = interest_blender - spot.location
    spot.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    spot.data.color = tuple(linear(value / 255) for value in (210, 80, 40))

    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'lights.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'lights.blend'))
    s = bpy.context.scene
    result = scene.apply(s, CLI, 'dotnet', tmp / 'lights.dat')
    assert result['lightChanged']
    run(CLI, 'dotnet', 'extract', tmp / 'lights.dat', '--session', tmp / 'exported')
    exported = read(tmp / 'exported/stage.json')
    lights = {light['id']: light for light_set in exported['lighting']['lightSets']
              for light in light_set['lights']}
    point_written, spot_written = lights[point_source['id']], lights[spot_source['id']]
    assert point_written['hidden']
    assert [round(value * 255) for value in point_written['color'][:3]] == [25, 100, 225]
    assert Vector(tuple(point_written['position'][key] for key in ('x', 'y', 'z'))) == point_game
    assert [round(value * 255) for value in spot_written['color'][:3]] == [210, 80, 40]
    assert (Vector(tuple(spot_written['position'][key] for key in ('x', 'y', 'z'))) - spot_game).length < 1e-5
    written_interest = Vector(tuple(spot_written['interest'][key] for key in ('x', 'y', 'z')))
    assert ((written_interest - spot_game).normalized()
            - (interest_game - spot_game).normalized()).length < 1e-5

print('LIGHT TYPE EXPORT PASS: point and spot transforms, colors, and visibility round-trip')
