"""Shared utilities for the online API harness. No local MinerU or model runtime."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'harness' / 'financial-report'

def read_json(path: Path):
    with path.open(encoding='utf-8-sig') as stream:
        return json.load(stream)

def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    temporary.replace(path)

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def doctor():
    from importlib.metadata import PackageNotFoundError, version
    versions, missing = {}, []
    for name in ['requests', 'pypdf', 'beautifulsoup4', 'sqlglot', 'jsonschema']:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            missing.append(name)
    token = os.environ.get('MINERU_API_TOKEN', '').strip()
    token_present = bool(token and token != 'YOUR_MINERU_API_TOKEN')
    errors = [f'Missing dependency: {name}' for name in missing]
    if not token_present:
        errors.append('Set MINERU_API_TOKEN for online parsing. Never use the Claude API key.')
    return {'status': 'ready' if not errors else 'needs_configuration',
            'mode': 'mineru_online_api', 'python': sys.executable,
            'versions': versions, 'mineru_token_configured': token_present,
            'errors': errors, 'network_tested': False}
