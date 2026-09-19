#!/usr/bin/env python3
"""Build a development add-on ZIP without game assets, caches, or backend binaries."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
source = root / 'blender_addon/melee_map_editor'
target = root / 'artifacts/melee-map-editor-0.1.0.zip'
target.parent.mkdir(exist_ok=True)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob('*.py')):
        archive.write(path, path.relative_to(source.parent))
print(target)
