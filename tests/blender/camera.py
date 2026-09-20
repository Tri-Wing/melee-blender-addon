"""Run with Blender 4.5: --background --factory-startup --python-exit-code 1 --python tests/blender/camera.py"""
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor
from melee_map_editor import scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-camera-blender-') as temporary:
    directory = Path(temporary) / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    source = stage['camera']
    imported = [obj for obj in bpy.context.scene.objects
                if obj.get('mme_role') == 'preview-camera']
    assert len(imported) == 1
    camera = imported[0]
    assert camera.type == 'CAMERA'
    assert bpy.context.scene.camera == camera
    assert camera.get('mme_preview_only')
    assert camera.get('mme_runtime_tracks_subjects')
    expected_position = Vector((source['position']['x'], -source['position']['z'], source['position']['y']))
    expected_interest = Vector((source['interest']['x'], -source['interest']['z'], source['interest']['y']))
    assert (camera.location - expected_position).length < 0.00001
    actual_direction = camera.rotation_euler.to_matrix() @ Vector((0, 0, -1))
    expected_direction = (expected_interest - expected_position).normalized()
    assert actual_direction.dot(expected_direction) > 0.999999
    assert math.isclose(camera.data.angle, math.radians(source['fieldOfViewDegrees']), rel_tol=1e-6)
    assert bpy.context.scene.render.resolution_x == 640
    assert bpy.context.scene.render.resolution_y == 480

    # This object is deliberately outside the protected DAT inventory.
    camera.location += Vector((20, 30, 40))
    scene.prepare(bpy.context.scene)
    bpy.data.objects.remove(camera, do_unlink=True)
    scene.prepare(bpy.context.scene)

print('BLENDER_CAMERA_OK: stage pose, orientation, FOV, active camera, and preview-only export behavior')
