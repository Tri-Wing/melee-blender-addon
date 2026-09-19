"""Preview light edits reach equivalent model-group sets used by stage code."""
import os
from pathlib import Path
import sys
import tempfile
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4

with tempfile.TemporaryDirectory(prefix='mme-light-sets-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrIz.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    assert stage['lighting']['previewSetId'] == 'group-000'
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    ambient = next(obj for obj in s.objects
                   if obj.get('mme_id') == 'group-000-light-000')
    ambient['mme_light_color'] = [linear(value / 255) for value in (20, 40, 200)]
    ambient['mme_light_intensity'] = .5

    result = scene.apply(s, CLI, 'dotnet', tmp / 'lights.dat')
    assert result['lightChanged']
    run(CLI, 'dotnet', 'extract', tmp / 'lights.dat', '--session', tmp / 'exported')
    exported = read(tmp / 'exported/stage.json')['lighting']['lightSets']
    colors = {light_set['id']: [round(value * 255) for value in light_set['lights'][0]['color'][:3]]
              for light_set in exported if light_set['id'].startswith('group-')}
    expected = [round(255 * (12.92 * (linear(value / 255) * .5)
                             if linear(value / 255) * .5 <= .0031308
                             else 1.055 * (linear(value / 255) * .5) ** (1 / 2.4) - .055))
                for value in (20, 40, 200)]
    assert colors['group-000'] == expected
    assert colors['group-001'] == expected
    assert colors['group-002'] == expected
    assert colors['group-003'] == expected
    assert colors['group-004'] == [128, 128, 128]

print('LIGHT SET EXPORT PASS: preview edits reach every equivalent runtime set')
