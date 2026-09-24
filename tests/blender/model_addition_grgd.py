"""Programmatic attachment discovery and export regression for GrGd.dat."""
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import model_additions, modeling, scene
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
    existing = [item for item in stage['modelAdditionTargets']
                if item['placement'] == 'existing-jobj']
    new_chains = [item for item in stage['modelAdditionTargets']
                  if item['placement'] == 'new-jobj-chain']
    assert len(existing) == 13 and new_chains

    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
    cube = bpy.context.object
    material = bpy.data.materials.new('GrGd Addition')
    material.diffuse_color = (0.25, 0.5, 0.75, 1)
    cube.data.materials.append(material)
    target = new_chains[0]['id']
    assert bpy.ops.mme.add_models(target_jobj_id=target) == {'FINISHED'}
    registered = model_additions.objects(bpy.context.scene)
    assert registered[0].parent.get('mme_role') == model_additions.DOBJ_ROLE
    dobj = registered[0].parent
    assert dobj.parent_type == 'BONE'
    generated_bone = dobj.parent.pose.bones[dobj.parent_bone]
    assert generated_bone.get('mme_role') == model_additions.JOBJ_ROLE
    assert generated_bone.parent is not None
    nodes = registered[0].active_material.node_tree.nodes
    assert nodes.get('Stage Surface') and nodes.get('Stage Diffuse Lighting')
    payload, assets = model_additions.edits(bpy.context.scene, stage)
    assert payload and not assets and payload['additions'][0]['targetJobjId'] == target
    assert payload['additions'][0]['placement'] == 'new-jobj-chain'
    assert payload['materials'][0]['preset'] == 'opaque-diffuse-texture-v2'

    result = scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'grgd-added.dat')
    assert result['modelChanged'] and result['modelTriangles'] == 12
    run(CLI, 'dotnet', 'extract', temporary / 'grgd-added.dat',
        '--session', temporary / 'reimport')
    reimported = read(temporary / 'reimport/stage.json')
    added = [preview for preview in reimported['modelPreviews']
             if preview.get('preview', {}).get('diffuseLighting')]
    assert added

    multi_pobj = sorted((info for info in modeling.targets(bpy.context.scene)
                         if info['groupIndex'] == 2 and info['jobjIndex'] == 7
                         and info['dobjIndex'] == 2), key=lambda info: info['pobjIndex'])
    assert len(multi_pobj) == 2
    assert all(modeling.allows(info, 'topologyReplacement') for info in multi_pobj)
    split_obj = modeling.target_object(bpy.context.scene, multi_pobj[1])
    assert split_obj.name.startswith(
        'Editable Model - Group 002 JOBJ 007 DOBJ 002 POBJ 001')
    split_obj.data.clear_geometry()
    split_obj.data.from_pydata([(0, 0, 0), (2, 0, 0), (0, 2, 0)], [], [(0, 1, 2)])
    split_obj.data.update()
    scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'grgd-split.dat')
    run(CLI, 'dotnet', 'extract', temporary / 'grgd-split.dat',
        '--session', temporary / 'split-reimport')
    split_stage = read(temporary / 'split-reimport/stage.json')
    same_joint = [info for info in split_stage['editableMeshes']
                  if info['groupIndex'] == 2 and info['jobjIndex'] == 7]
    retained = next(info for info in same_joint if info['dobjIndex'] == 2)
    separated = max(same_joint, key=lambda info: info['dobjIndex'])
    separated_mesh = read(temporary / 'split-reimport' / separated['file'])
    assert retained['pobjIndex'] == 0
    assert separated['dobjIndex'] != 2 and separated['pobjIndex'] == 0
    assert len(separated_mesh['positions']) == 3
    assert len(separated_mesh['triangleIndices']) == 3

    bpy.data.objects.remove(modeling.target_object(bpy.context.scene, multi_pobj[1]), do_unlink=True)
    scene.apply(bpy.context.scene, CLI, 'dotnet', temporary / 'grgd-deleted.dat')
    run(CLI, 'dotnet', 'extract', temporary / 'grgd-deleted.dat',
        '--session', temporary / 'deleted-reimport')
    deleted_stage = read(temporary / 'deleted-reimport/stage.json')
    remaining = [info for info in deleted_stage['editableMeshes']
                 if info['groupIndex'] == 2 and info['jobjIndex'] == 7
                 and info['dobjIndex'] == 2]
    assert len(remaining) == 1 and remaining[0]['pobjIndex'] == 0

    bpy.data.objects.remove(registered[0], do_unlink=True)
    assert not model_additions.objects(bpy.context.scene)
    assert model_additions.edits(bpy.context.scene, stage) == (None, {})

print('GrGd programmatic model addition test passed')
