"""Supported source material fields shared by the panel, preview and DAT export."""
import json
import math
import struct
from pathlib import Path
from . import surface
from .protocol import StageError

TRANSPARENCY = {'OPAQUE': 0, 'ALPHA': 1, 'ADDITIVE': 2, 'SUBTRACT': 3, 'CUSTOM': 4}
ALPHA_SOURCES = {'COMPATIBILITY': 0, 'MATERIAL': 1, 'VERTEX': 2, 'MULTIPLY': 3}
RENDER_FLAGS = (
    ('mme_diffuse_lighting', 2), ('mme_specular_lighting', 3), ('mme_toon_shading', 12),
    ('mme_depth_offset', 24), ('mme_effect', 25), ('mme_shadow', 26),
    ('mme_depth_always', 27), ('mme_all_textures', 28), ('mme_no_depth_write', 29),
    ('mme_user_render_flag', 31))


def linear(value):
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def byte(value):
    if not math.isfinite(value):
        raise StageError('Material colors must be finite.')
    encoded = 12.92 * value if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055
    return max(0, min(255, round(encoded * 255)))


def initialize(material, entry, definition, directory, stage):
    definition = dict(definition)
    preview = entry.get('preview', {})
    if 'useVertexColor' not in definition:
        definition['useVertexColor'] = preview.get('useVertexColor', not definition.get('canEditDiffuse', True))
        definition['canToggleVertexColor'] = definition['useVertexColor']
    preview_alpha = preview.get('alpha', {})
    if 'alphaSource' not in definition:
        definition['alphaSource'] = (3 if preview_alpha.get('vertex') and preview_alpha.get('multiplyMaterial') else
                                     2 if preview_alpha.get('vertex') else 1)
    if 'transparencyMode' not in definition:
        blend = preview_alpha.get('blendMode')
        source = preview_alpha.get('sourceFactor')
        destination = preview_alpha.get('destinationFactor')
        definition['transparencyMode'] = (0 if blend == 0 else 1 if blend == 1 and source == 4 and destination == 5
                                          else 2 if blend == 1 and source in (1, 4) and destination == 1
                                          else 3 if blend == 3 else 4)
        definition['renderFlags'] = None
        definition['editableRenderFlagsMask'] = 0
    material['mme_material_definition'] = json.dumps(definition)
    material['mme_material_preview'] = json.dumps(entry['preview'])
    material['mme_material_directory'] = str(directory)
    texture = entry['preview'].get('texture')
    material['mme_material_preview_files'] = json.dumps([f for f in stage['baselineFiles']
        if texture and f['file'] == texture['file']])
    material['mme_material_updating'] = True
    try:
        material.mme_diffuse = [linear(c / 255) for c in definition['diffuse']]
        material.mme_alpha = definition['alpha']
        material.mme_texture_blend = definition['textureBlend'] if definition['textureBlend'] is not None else 1
        material.mme_use_vertex_color = definition['useVertexColor']
        material.mme_alpha_source = next(name for name, value in ALPHA_SOURCES.items()
                                         if value == definition['alphaSource'])
        material['mme_alpha_source_initialized'] = True
        material['mme_vertex_mode_initialized'] = True
        material.mme_transparency = next(name for name, value in TRANSPARENCY.items()
                                         if value == definition['transparencyMode'])
        if definition['renderFlags'] is not None:
            for name, bit in RENDER_FLAGS:
                setattr(material, name, bool(definition['renderFlags'] & (1 << bit)))
        material['mme_render_settings_initialized'] = True
    finally:
        material['mme_material_updating'] = False


def definition(material):
    info = json.loads(material.get('mme_material_definition', 'null')) if material else None
    if info and 'useVertexColor' not in info:
        preview = json.loads(material.get('mme_material_preview', '{}'))
        info['useVertexColor'] = preview.get('useVertexColor', not info.get('canEditDiffuse', True))
        info['canToggleVertexColor'] = info['useVertexColor']
        material['mme_material_definition'] = json.dumps(info)
    if info and 'transparencyMode' not in info:
        preview = json.loads(material.get('mme_material_preview', '{}'))
        alpha = preview.get('alpha', {})
        blend, source, destination = (alpha.get(key) for key in ('blendMode', 'sourceFactor', 'destinationFactor'))
        info['transparencyMode'] = (0 if blend == 0 else 1 if blend == 1 and source == 4 and destination == 5
                                    else 2 if blend == 1 and source in (1, 4) and destination == 1
                                    else 3 if blend == 3 else 4)
        info['renderFlags'] = None
        info['editableRenderFlagsMask'] = 0
        material['mme_material_definition'] = json.dumps(info)
    if info and 'alphaSource' not in info:
        preview = json.loads(material.get('mme_material_preview', '{}'))
        alpha = preview.get('alpha', {})
        info['alphaSource'] = (3 if alpha.get('vertex') and alpha.get('multiplyMaterial') else
                               2 if alpha.get('vertex') else 1)
        material['mme_material_definition'] = json.dumps(info)
    if info and not material.get('mme_vertex_mode_initialized'):
        material['mme_material_updating'] = True
        try:
            material.mme_use_vertex_color = info['useVertexColor']
            material['mme_vertex_mode_initialized'] = True
        finally:
            material['mme_material_updating'] = False
    if info and not material.get('mme_alpha_source_initialized'):
        material['mme_material_updating'] = True
        try:
            material.mme_alpha_source = next(name for name, value in ALPHA_SOURCES.items()
                                             if value == info['alphaSource'])
            material['mme_alpha_source_initialized'] = True
        finally:
            material['mme_material_updating'] = False
    if info and not material.get('mme_render_settings_initialized'):
        material['mme_material_updating'] = True
        try:
            material.mme_transparency = next(name for name, value in TRANSPARENCY.items()
                                             if value == info['transparencyMode'])
            if info['renderFlags'] is not None:
                for name, bit in RENDER_FLAGS:
                    setattr(material, name, bool(info['renderFlags'] & (1 << bit)))
            material['mme_render_settings_initialized'] = True
        finally:
            material['mme_material_updating'] = False
    return info


def update(material, context):
    if material.get('mme_material_updating') or not definition(material):
        return
    material['mme_material_updating'] = True
    try:
        preview = json.loads(material['mme_material_preview'])
        preview['useVertexColor'] = material.mme_use_vertex_color
        preview['diffuseLighting'] = material.mme_diffuse_lighting
        preview['specularLighting'] = material.mme_specular_lighting
        preview['color'] = [byte(c) / 255 for c in material.mme_diffuse] + [1]
        if preview.get('alpha'):
            preview['alpha']['material'] = material.mme_alpha
            preview['alpha']['textureBlend'] = material.mme_texture_blend
            alpha_source = ALPHA_SOURCES[material.mme_alpha_source]
            vertex_alpha = (material.mme_use_vertex_color if alpha_source == 0 else alpha_source in (2, 3))
            preview['alpha']['vertex'] = vertex_alpha
            preview['alpha']['multiplyMaterial'] = alpha_source == 3
            transparency = TRANSPARENCY[material.mme_transparency]
            if transparency != 4:
                preview['alpha']['blendMode'] = 0 if transparency == 0 else 3 if transparency == 3 else 1
                preview['alpha']['sourceFactor'] = 4 if transparency == 1 else 1
                preview['alpha']['destinationFactor'] = 5 if transparency == 1 else 1 if transparency >= 2 else 5
        if preview.get('texture'):
            preview['texture']['colorBlend'] = material.mme_texture_blend
        color_node = material.node_tree.nodes.get('Stage Vertex Color') if material.node_tree else None
        color_layer = color_node.layer_name if color_node else material.get('mme_vertex_color_layer', 'Stage Color 0')
        surface.configure_preview(material, preview, Path(material['mme_material_directory']),
                                  {'baselineFiles': json.loads(material['mme_material_preview_files'])})
        if color_layer and material.mme_use_vertex_color:
            surface.configure_color_preview(material, color_layer)
        material.pop('mme_material_error', None)
    except Exception as error:
        material['mme_material_error'] = str(error)
    finally:
        material['mme_material_updating'] = False


def edits(scene, stage):
    declared = {entry['id']: entry for entry in stage.get('editableMaterialProperties', [])}
    used = {slot.material for obj in scene.objects if obj.type == 'MESH'
            and obj.get('mme_session_id') == scene.mme_session_id for slot in obj.material_slots if slot.material}
    values_by_id = {}
    for material in used:
        baseline = definition(material)
        if baseline is None:
            continue
        key = baseline['id']
        if key not in declared or material.get('mme_model_material_source') != stage['source']['sha256']:
            raise StageError('Assigned material properties belong to another stage or an unsupported material.')
        if material.get('mme_material_error'):
            raise StageError(f"{material.name}: {material['mme_material_error']}")
        source = declared[key]
        source_use_vertex_color = source.get('useVertexColor', baseline['useVertexColor'])
        source_can_toggle = source.get('canToggleVertexColor', baseline['canToggleVertexColor'])
        rgb = [byte(c) for c in material.mme_diffuse]
        alpha, blend = material.mme_alpha, material.mme_texture_blend
        use_vertex_color = material.mme_use_vertex_color
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (alpha, blend)):
            raise StageError('Material alpha and texture blend must be finite values between zero and one.')
        value = {'id': key}
        alpha_source = ALPHA_SOURCES[material.mme_alpha_source]
        source_alpha_source = source.get('alphaSource', baseline['alphaSource'])
        if alpha_source != source_alpha_source:
            if alpha_source in (2, 3) and not baseline['canToggleVertexColor']:
                raise StageError('Vertex alpha requires an imported Stage Color 0 channel.')
            value['alphaSource'] = alpha_source
        transparency = TRANSPARENCY[material.mme_transparency]
        source_transparency = source.get('transparencyMode', baseline['transparencyMode'])
        if transparency != source_transparency:
            if transparency == 4:
                raise StageError('Custom transparency can be preserved but not created from a standard mode.')
            value['transparencyMode'] = transparency
        source_flags = source.get('renderFlags', baseline.get('renderFlags'))
        editable_mask = source.get('editableRenderFlagsMask', baseline.get('editableRenderFlagsMask', 0))
        if source_flags is not None and editable_mask:
            render_flags = source_flags
            for name, bit in RENDER_FLAGS:
                if not editable_mask & (1 << bit):
                    continue
                render_flags = ((render_flags | (1 << bit)) if getattr(material, name)
                                else (render_flags & ~(1 << bit)))
            if render_flags != source_flags:
                value['renderFlags'] = render_flags
        if use_vertex_color != source_use_vertex_color:
            if not source_can_toggle:
                raise StageError('Vertex-color mode cannot be changed for this material.')
            value['useVertexColor'] = use_vertex_color
        if rgb != source['diffuse']:
            if not source['canEditDiffuse'] and use_vertex_color:
                raise StageError('This material uses vertex color; diffuse color editing is not supported.')
            value['diffuse'] = rgb
        if struct.pack('f', alpha) != struct.pack('f', source['alpha']):
            if not source['canEditAlpha'] and not (alpha_source in (1, 3) or not use_vertex_color):
                raise StageError('This material uses vertex alpha; material alpha editing is not supported.')
            value['alpha'] = alpha
        original_blend = source['textureBlend'] if source['textureBlend'] is not None else 1
        if struct.pack('f', blend) != struct.pack('f', original_blend):
            if not source['canEditBlend']:
                raise StageError('This material does not use a supported texture blend operation.')
            value['textureBlend'] = blend
        if key in values_by_id and values_by_id[key] != value:
            raise StageError('Copies of one stage material have conflicting properties. Use a single material datablock for that stage material.')
        values_by_id[key] = value
    changed = [value for value in values_by_id.values() if len(value) > 1]
    changed.sort(key=lambda value: value['id'])
    return {'protocolVersion': 2, 'materials': changed} if changed else None
