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
    bpy.ops.object.select_all(action='DESELECT')
    registered[0].select_set(True)
    bpy.context.view_layer.objects.active = registered[0]
    assert model_additions.remove_selected(bpy.context) == 1
    assert not model_additions.objects(bpy.context.scene)
    assert not any(bone.get('mme_role') == model_additions.JOBJ_ROLE
                   for obj in bpy.context.scene.objects if obj.type == 'ARMATURE'
                   for bone in obj.pose.bones)

print('GrGd programmatic model addition test passed')
