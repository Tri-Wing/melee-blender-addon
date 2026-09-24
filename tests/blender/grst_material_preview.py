"""Static vertex-color materials have texture previews outside the export catalog."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, surface
from melee_map_editor.protocol import read, run
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
with tempfile.TemporaryDirectory(prefix='mme-grst-preview-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrSt.dat', '--session', tmp / 'session')
    stage = read(tmp / 'session/stage.json')
    group = read(tmp / 'session/models/group-003/group.json')
    nodes = {n['id']: n for n in group['nodes']}
    dobj = next(n for n in nodes.values() if n['kind'] == 'dobj' and n['index'] == 13
                and nodes[n['ownerId']]['index'] == 4)
    source = next(m for name in group['meshes']
                  for m in [read(tmp / 'session/models/group-003' / name)] if m['ownerId'] == dobj['id'])
    info = next(i for i in stage['editableMeshes'] if i['id'] == source['id'])
    assert modeling.allows(info, 'topologyReplacement')
    assert source['texCoords0'] and source['colors0']
    assert source['id'] not in {m['id'] for m in stage['modelMaterials']}
    preview = next(m for m in stage['modelPreviews'] if m['id'] == source['id'])
    assert preview['preview']['texture'] and preview['preview']['warning'] is None
    scene.import_session(bpy.context, tmp / 'session')
    s = bpy.context.scene
    obj = modeling.target_object(s, info)
    material = obj.active_material
    assert surface.material_id(material) is None
    assert material.get('mme_preview_model_id') == source['id']
    nodes = material.node_tree.nodes
    assert nodes['Stage Texture'].image.packed_file
    assert nodes['Stage Vertex Color'].layer_name == 'Stage Color 0'
    assert nodes['Stage Color Modulation'].inputs[1].links[0].from_node == nodes['Stage Diffuse Tint']
    assert tuple(nodes['Stage Diffuse Tint'].inputs[1].default_value) == (1, 1, 1, 1)
    assert nodes['Stage Diffuse Tint'].inputs[2].links[0].from_node == nodes['Stage Texture']
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrSt.dat').read_bytes()
    obj.data.vertices[0].co.z += 1
    scene.apply(s, CLI, 'dotnet', tmp / 'moved.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'moved.dat', '--session', tmp / 'exported')
    new_group = read(tmp / 'exported/models/group-003/group.json')
    new_source = next(m for name in new_group['meshes']
                      for m in [read(tmp / 'exported/models/group-003' / name)]
                      if m['sourceOffset'] == source['sourceOffset'])
    assert new_source['positions'] != source['positions']
    for field in ('colors0', 'colors1', 'texCoords0', 'normals', 'triangleIndices'):
        assert new_source[field] == source[field]
    materials = stage['modelMaterials'] + stage['modelPreviews']
    print(f"GrSt: {len(materials)} material previews, {sum(bool(m['preview']['texture']) for m in materials)} textured")
print('GRST MATERIAL PREVIEW PASS')
