"""Collada material-graph integration fixture for external-model addition."""
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import model_additions, scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
BOAT = ROOT / 'example_assets/wind_waker_boat/Salvage Boat.dae'

if not BOAT.is_file():
    print('model addition DAE test skipped: local fixture is absent')
    raise SystemExit(0)
if not hasattr(bpy.types, 'WM_OT_collada_import'):
    print('model addition DAE test skipped: this Blender build has no Collada importer')
    raise SystemExit(0)

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-addition-dae-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrGd.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    bpy.ops.wm.collada_import(filepath=str(BOAT))
    imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    assert imported
    target = next(item for item in stage['modelAdditionTargets']
                  if item['placement'] == 'new-jobj-chain')
    model_additions.register_selected(bpy.context, target['id'])
    payload, assets = model_additions.edits(bpy.context.scene, stage)
    assert payload and len(payload['images']) == 1 and assets
    assert payload['materials'][0]['preset'] == 'opaque-diffuse-texture-v2'
    result = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'boat-dae.dat')
    assert result['modelChanged'] and result['modelTriangles'] > 0

print('model addition DAE integration test passed')
