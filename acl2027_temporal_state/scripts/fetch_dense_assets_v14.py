#!/usr/bin/env python3
"""Acquire/check pinned public BGE/CPU wheels; never install or execute a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, item: dict) -> dict:
    if path.stat().st_size != item['bytes'] or sha_file(path) != item['sha256']:
        raise ValueError(f'asset hash/size mismatch: {item["relative_path"]}')
    if item.get('git_blob_sha1'):
        data = path.read_bytes()
        digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if digest != item['git_blob_sha1']:
            raise ValueError('tokenizer Git blob identity mismatch')
    return {k: item[k] for k in ('relative_path', 'bytes', 'sha256')}


def fetch(config_path: Path, asset_dir: Path, *, download=False, scope='all') -> dict:
    if asset_dir.resolve().is_relative_to(ROOT.parent):
        raise ValueError('assets must remain external to the repository')
    config = json.loads(config_path.read_text())
    items = ([] if scope == 'wheels' else config['assets']) + ([] if scope == 'model' else config['runtime']['wheels'])
    if scope not in ('all', 'model', 'wheels'):
        raise ValueError('unknown scope')
    if download:
        asset_dir.mkdir(parents=True, exist_ok=True)
        missing_bytes = sum(x['bytes'] for x in items if not (asset_dir / x['relative_path']).exists())
        reserve = config['acquisition']['minimum_free_bytes_before_download']
        if shutil.disk_usage(asset_dir).free < missing_bytes + reserve:
            raise ValueError('insufficient disk for missing downloads plus declared free reserve')
    verified, missing = [], []
    for item in items:
        relative = Path(item['relative_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe relative asset path')
        path = asset_dir / relative
        if not path.exists() and download:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.partial')
            if temporary.exists():
                raise FileExistsError(f'preserved incomplete download: {temporary}')
            try:
                request = urllib.request.Request(item['url'], headers={'User-Agent': 'WikiGraphRag-pinned-assets/0.14'})
                with urllib.request.urlopen(request, timeout=120) as response, temporary.open('xb') as stream:
                    received = 0
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        received += len(block)
                        if received > item['bytes']:
                            raise ValueError('server sent more than the declared asset size')
                        stream.write(block)
                verify(temporary, item)
                temporary.rename(path)
            except Exception:
                # Leave the named partial artifact for explicit inspection/removal.
                raise
        if path.exists():
            verified.append(verify(path, item))
        else:
            missing.append(item['relative_path'])
    return {'schema_version': 'dense_asset_receipt_v0.14', 'config_sha256': sha_file(config_path),
            'fetch_script_sha256': sha_file(Path(__file__)), 'external_asset_directory': str(asset_dir.resolve()),
            'download_requested': download, 'scope': scope, 'verified': verified, 'missing': missing,
            'model_executed': False, 'packages_installed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/dense_retriever_candidate_v14.json')
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--scope', choices=('all', 'model', 'wheels'), default='all')
    parser.add_argument('--download', action='store_true', help='explicitly acquire missing public assets')
    parser.add_argument('--receipt', type=Path, help='new JSON receipt path; no raw model/source contents')
    args = parser.parse_args()
    if args.receipt and args.receipt.exists():
        raise FileExistsError('refusing to overwrite receipt')
    receipt = fetch(args.config, args.assets, download=args.download, scope=args.scope)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open('x') as stream:
            json.dump(receipt, stream, indent=2); stream.write('\n')
    print(json.dumps({'verified_count': len(receipt['verified']), 'missing': receipt['missing']}))
    if receipt['missing']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
