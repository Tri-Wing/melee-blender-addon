"""Run with Blender 4.5: --background --factory-startup --python-exit-code 1 --python tests/blender/atmosphere.py"""
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor
from melee_map_editor import scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
linear = lambda value: value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-atmosphere-blender-') as temporary:
    directory = Path(temporary) / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrGb.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    world = bpy.context.scene.world
    source = stage['atmosphere']
    assert world and world.get('mme_role') == 'preview-atmosphere'
    assert world.get('mme_preview_only')
    assert world.get('mme_fog_id') == source['previewFogId']
    background = world.node_tree.nodes['Background']
    actual = background.inputs['Color'].default_value
    expected = [linear(component) for component in source['backgroundColor'][:3]]
    assert all(math.isclose(actual[index], expected[index], rel_tol=1e-6) for index in range(3))
    assert background.inputs['Strength'].default_value == 1

    # World preview settings are outside the DAT edit inventory.
    background.inputs['Color'].default_value = (0.1, 0.2, 0.3, 1)
    scene.prepare(bpy.context.scene)

print('BLENDER_ATMOSPHERE_OK: fog clear color drives the preview-only World background')
