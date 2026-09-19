"""Read-only, live collision selection details; assignment controls are separate."""
from functools import lru_cache
from pathlib import Path
import bmesh
from . import collision, materials
from .protocol import StageError, read


@lru_cache(maxsize=8)
def _source(path, modified, size):
    return read(path)


def source(directory):
    path = Path(directory) / 'collision/collision.json'
    stat = path.stat()
    return _source(str(path), stat.st_mtime_ns, stat.st_size)


def describe(obj, original):
    if obj.mode != 'EDIT':
        raise StageError('Enter Collision Editing and select an edge.')
    bm = bmesh.from_edit_mesh(obj.data)
    selected = [e for e in bm.edges if e.select and not e.hide]
    if not selected:
        raise StageError('Select a collision edge to inspect it.')
    edge = selected[0] if len(selected) == 1 else bm.select_history.active
    if not isinstance(edge, bmesh.types.BMEdge) or edge not in selected:
        raise StageError(f'{len(selected)} edges selected. Select one edge, or make an edge active in Edge Select mode.')
    layers = {name: bm.edges.layers.int.get('mme_' + name) for name in collision.ATTRS}
    vertex = bm.verts.layers.int.get('mme_vertex')
    if vertex is None or any(layer is None for layer in layers.values()):
        raise StageError('Collision metadata is missing. Undo the unsupported edit or re-import.')
    values = {name: edge[layer] for name, layer in layers.items()}
    handle, category, joint = values['line'], values['category'], values['joint']
    if handle <= 0 or not 0 <= category < len(collision.CATEGORIES) or not 1 <= joint <= len(original['joints']):
        raise StageError('Invalid edge identity, type, or joint. Validate the stage for details.')
    endpoints = {v[vertex]: v for v in edge.verts}
    if values['start'] == values['end'] or set(endpoints) != {values['start'], values['end']}:
        raise StageError('Edge direction metadata does not match its vertices. Undo the unsupported edit.')
    a, b = endpoints[values['start']], endpoints[values['end']]
    surface = values['low'] & 255
    baseline = original['lines'][handle-1] if handle <= len(original['lines']) else None
    adjacent = {}
    for name, v in (('start', a), ('end', b)):
        adjacent[name] = sorted(e[layers['line']] for e in v.link_edges if e != edge)
    return {
        'selected': len(selected), 'handle': handle, 'category': collision.CATEGORIES[category],
        'surface': surface, 'surfaceName': materials.NAMES[surface] if surface < len(materials.NAMES) else 'Custom / Unknown',
        'drop': bool(values['low'] & 0x100), 'ledge': bool(values['low'] & 0x200),
        'disabled': bool(values['high'] & 0x80), 'joint': joint,
        'length': (b.co-a.co).length, 'start': values['start'], 'end': values['end'],
        'startPosition': tuple(a.co), 'endPosition': tuple(b.co), 'adjacent': adjacent,
        'id': collision.identity(original, 'lines', handle), 'jointId': original['joints'][joint-1]['id'],
        'startId': collision.identity(original, 'vertices', values['start']),
        'endId': collision.identity(original, 'vertices', values['end']),
        'high': values['high'], 'low': values['low'],
        'exportHigh': (values['high'] & ~15) | (1 << category) if category < 4 else None,
        'baseline': baseline,
    }
