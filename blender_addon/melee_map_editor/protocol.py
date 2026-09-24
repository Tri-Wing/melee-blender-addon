"""Versioned, shell-free CLI boundary. Baseline session files are immutable."""
import hashlib
import json
from pathlib import Path
import subprocess

SESSION_PROTOCOL = 3
SESSION_SCHEMA = 2
MODEL_OPERATIONS = ('vertexMovement', 'topologyReplacement', 'uvEditing',
                    'vertexColorEditing', 'materialAssignment',
                    'materialPropertyEditing', 'wholeObjectDeletion')


class StageError(RuntimeError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def run(cli, dotnet, *args):
    path = Path(cli).expanduser()
    if not path.is_file():
        raise StageError('Set the MeleeMap CLI path in add-on preferences first.')
    command = ([dotnet or 'dotnet', str(path)] if path.suffix.lower() == '.dll'
               else [str(path)])
    try:
        result = subprocess.run(command + list(map(str, args)), capture_output=True,
                                text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StageError(f'Cannot run MeleeMap: {exc}') from exc
    try:
        envelope = json.loads(result.stdout)
    except ValueError as exc:
        raise StageError(f'Invalid CLI response: {result.stderr[-1000:]}') from exc
    if envelope.get('protocolVersion') != 1:
        raise StageError('Unsupported CLI protocol; use the matching backend build.')
    if result.returncode or not envelope.get('ok'):
        error = envelope.get('error', {})
        raise StageError(f"{error.get('code', 'CLI_ERROR')}: {error.get('message', result.stderr)}")
    return envelope['data']


def load_session(directory):
    directory = Path(directory).resolve()
    stage = read(directory / 'stage.json')
    if (stage.get('protocolVersion'), stage.get('schemaVersion')) != (SESSION_PROTOCOL, SESSION_SCHEMA):
        raise StageError(f'This add-on requires session protocol {SESSION_PROTOCOL}, schema {SESSION_SCHEMA}. Rebuild/update and re-import the DAT.')
    if not isinstance(stage.get('editableMeshes'), list) or 'editableMesh' in stage:
        raise StageError('This session uses an obsolete model manifest. Re-import the DAT.')
    for info in stage['editableMeshes']:
        capabilities = info.get('operationCapabilities')
        if not isinstance(capabilities, dict) or any(
                not isinstance(capabilities.get(operation), dict)
                or not isinstance(capabilities[operation].get('allowed'), bool)
                for operation in MODEL_OPERATIONS):
            raise StageError('This session has missing model capabilities. Re-import the DAT.')
    if stage['coordinates']['blenderFromGame'] != '(X, -Z, Y)':
        raise StageError('Unsupported session coordinate transform.')
    for entry in stage['baselineFiles']:
        path = (directory / entry['file']).resolve()
        if not path.is_relative_to(directory):
            raise StageError('Session path escapes its directory.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise StageError(f"Session baseline changed: {entry['file']}")
    if hashlib.sha256((directory / 'source.dat').read_bytes()).hexdigest() != stage['source']['sha256']:
        raise StageError('Session source hash mismatch.')
    return stage
