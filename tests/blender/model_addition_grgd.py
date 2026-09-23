"""Programmatic attachment discovery and export regression for GrGd.dat."""
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
SOURCE = CORPUS / 'GrGd.dat'

if not SOURCE.is_file():
    print('GrGd model addition test skipped: fixture is absent')
    raise SystemExit(0)

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-addition-grgd-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', SOURCE, '--session', directory)
    scene.import_session(bpy.context, directory)
    stage = read(directory / 'stage.json')
    assert stage['capabilities']['modelAddition']
    assert len(stage['modelAdditionTargets']) == 13

    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
    cube = bpy.context.object
    material = bpy.data.materials.new('GrGd Addition')
    material.diffuse_color = (0.25, 0.5, 0.75, 1)
    cube.data.materials.append(material)
    target = stage['modelAdditionTargets'][0]['id']
    assert bpy.ops.mme.add_models(target_jobj_id=target) == {'FINISHED'}
    payload, assets = model_additions.edits(bpy.context.scene, stage)
    assert payload and not assets and payload['additions'][0]['targetJobjId'] == target

    result = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'grgd-added.dat')
    assert result['modelChanged'] and result['modelTriangles'] == 12
    run(CLI, 'dotnet', 'extract', temporary / 'grgd-added.dat',
        '--session', temporary / 'reimport')

print('GrGd programmatic model addition test passed')
