#!/usr/bin/env python3
"""Fetch only the two frozen v0.7 backend assets, or verify without network.

Stdlib only. Existing corrupt files fail; this command never repairs, replaces,
or substitutes them. Downloads and extraction are staged and hash-checked.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import ssl
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT / 'configs/local_backend_v07.json'
DEFAULT_ASSETS = PROJECT.parent / 'tmp/local_backend_v07'
MAX_DOWNLOAD_SECONDS = 1800
SOCKET_TIMEOUT_SECONDS = 30
BLOCK_BYTES = 1024 * 1024

_spec = importlib.util.spec_from_file_location('bootstrap_backend_v07', PROJECT / 'scripts/local_backend_v07.py')
backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backend)


def _single_name(value: str) -> str:
    if not isinstance(value, str) or not value or value in {'.', '..'} or '/' in value or '\\' in value:
        raise ValueError(f'Expected one safe path component: {value!r}')
    return value


def _load_config() -> dict:
    config = json.loads(CONFIG.read_text())
    if config.get('schema_version') != 'local_backend_v0.7':
        raise ValueError('Unexpected pinned backend configuration version')
    assets = config.get('assets', [])
    if len(assets) != 2 or {s['relative_path'] for s in assets} != {
            'Qwen3-0.6B-Q8_0.gguf', 'llama-b11146-bin-ubuntu-x64.tar.gz'}:
        raise ValueError('Bootstrap supports exactly the two frozen v0.7 assets')
    if config['runtime_directory'] != 'runtime/llama-b11146':
        raise ValueError('Unexpected runtime extraction directory')
    for spec in assets:
        _single_name(spec['relative_path'])
        url = urllib.parse.urlsplit(spec['url'])
        if url.scheme != 'https' or url.hostname not in {'huggingface.co', 'github.com'} or url.username or url.password:
            raise ValueError('Asset URL must be the configured public HTTPS source')
        if type(spec['bytes']) is not int or not 0 < spec['bytes'] <= 640_000_000:
            raise ValueError('Invalid pinned asset byte bound')
        if not isinstance(spec['sha256'], str) or len(spec['sha256']) != 64 or any(c not in '0123456789abcdef' for c in spec['sha256']):
            raise ValueError('Invalid pinned asset digest')
    files = config.get('runtime_files', [])
    if len(files) != 60 or len({f['path'] for f in files}) != 60:
        raise ValueError('Expected exactly 60 unique frozen runtime file entries')
    for row in files:
        _single_name(row['path'])
    return config


def verify_asset(path: Path, spec: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Asset must be a regular file: {path}')
    if path.stat().st_size != spec['bytes'] or backend.digest(path) != spec['sha256']:
        raise ValueError(f'Existing asset size/hash mismatch: {path}; no automatic overwrite or substitution')


def inspect_runtime(runtime: Path, config: dict) -> list[dict]:
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError(f'Runtime must be a real directory: {runtime}')
    expected = {row['path']: row for row in config['runtime_files']}
    children = list(runtime.iterdir())
    if {p.name for p in children} != set(expected):
        raise ValueError('Runtime has missing or unexpected entries')
    for path in children:
        if path.is_symlink():
            _single_name(os.readlink(path))
            try:
                target = path.resolve(strict=True)
            except (RuntimeError, OSError) as error:
                raise ValueError(f'Broken or cyclic runtime symlink: {path.name}') from error
            if target.parent != runtime.resolve() or target.name not in expected or not target.is_file():
                raise ValueError(f'Runtime symlink escapes expected files: {path.name}')
        if not path.is_file():
            raise ValueError(f'Runtime entry is not a file: {path.name}')
    files = [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': backend.digest(p)} for p in sorted(children)]
    if files != config['runtime_files']:
        raise ValueError('Runtime file manifest mismatch; no automatic replacement')
    return files


class HTTPSRedirectsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing non-HTTPS asset redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_asset(asset_root: Path, spec: dict) -> None:
    """Stream one missing asset within exact length/hash and bounded time."""
    destination = asset_root / spec['relative_path']
    if destination.exists() or destination.is_symlink():
        verify_asset(destination, spec)
        return
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), HTTPSRedirectsOnly())
    request = urllib.request.Request(spec['url'], headers={
        'User-Agent': 'ACL2027-temporal-state-reproduction/0.7', 'Accept-Encoding': 'identity'})
    started = time.monotonic()
    fd, temporary_name = tempfile.mkstemp(prefix='.download-v07-', suffix='.part', dir=asset_root)
    temporary = Path(temporary_name)
    try:
        count, checksum = 0, sha256()
        with os.fdopen(fd, 'wb') as output, opener.open(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
            if urllib.parse.urlsplit(response.url).scheme != 'https' or response.status != 200:
                raise ValueError('Asset download requires successful HTTPS response')
            content_length = response.headers.get('Content-Length')
            if content_length is not None and int(content_length) != spec['bytes']:
                raise ValueError('HTTP Content-Length differs from pinned size')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('Unexpected HTTP content encoding')
            while True:
                if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                    raise TimeoutError('Asset download exceeded 1800-second wall budget')
                block = response.read(min(BLOCK_BYTES, spec['bytes'] - count + 1))
                if not block:
                    break
                count += len(block)
                if count > spec['bytes']:
                    raise ValueError('Asset download exceeded pinned byte length')
                output.write(block)
                checksum.update(block)
            output.flush()
            os.fsync(output.fileno())
        if count != spec['bytes'] or checksum.hexdigest() != spec['sha256']:
            raise ValueError('Downloaded asset length/hash mismatch')
        # Atomic no-overwrite installation. A concurrent valid installation is
        # accepted only after checking it; no existing destination is replaced.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            verify_asset(destination, spec)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_archive(archive: Path, config: dict) -> list[dict]:
    """Allow only the pinned flat directory, ordinary files and internal links."""
    prefix = PurePosixPath(config['runtime_directory']).name
    expected = {row['path']: row for row in config['runtime_files']}
    members, seen, links, regular = [], set(), {}, set()
    with tarfile.open(archive, mode='r:gz') as tf:
        for member in tf:
            if len(members) >= 61 or member.name in seen:
                raise ValueError('Unexpected duplicate or excess tar member')
            seen.add(member.name)
            parts = PurePosixPath(member.name).parts
            if member.name == prefix:
                if not member.isdir():
                    raise ValueError('Pinned tar root must be a directory')
            elif len(parts) == 2 and parts[0] == prefix and parts[1] in expected and member.name == '/'.join(parts):
                name = parts[1]
                if member.isreg():
                    if member.size != expected[name]['bytes']:
                        raise ValueError(f'Tar member byte bound mismatch: {name}')
                    regular.add(name)
                elif member.issym():
                    target = _single_name(member.linkname)
                    if target not in expected or member.size != 0:
                        raise ValueError(f'Unexpected tar symlink target: {name}')
                    links[name] = target
                else:
                    raise ValueError('Tar devices, hardlinks and other special entries are not allowed')
            else:
                raise ValueError(f'Tar member escapes the pinned flat runtime: {member.name!r}')
            members.append({'name': member.name, 'size': member.size,
                            'kind': 'directory' if member.isdir() else 'symlink' if member.issym() else 'file',
                            'linkname': member.linkname, 'mode': member.mode & 0o755})
    if seen != {prefix} | {prefix + '/' + name for name in expected}:
        raise ValueError('Tar members do not exactly match the runtime manifest')
    for source in links:
        target, visited = source, set()
        while target in links:
            if target in visited:
                raise ValueError('Cyclic tar symlink')
            visited.add(target)
            target = links[target]
        if target not in regular or expected[source] != {**expected[target], 'path': source}:
            raise ValueError('Tar symlink does not resolve to its pinned file content')
    return members


def extract_runtime(asset_root: Path, archive: Path, config: dict) -> None:
    runtime = asset_root / config['runtime_directory']
    if runtime.exists() or runtime.is_symlink():
        inspect_runtime(runtime, config)
        return
    layout = inspect_archive(archive, config)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if runtime.parent.is_symlink():
        raise ValueError('Refusing symlink runtime parent')
    prefix = runtime.name
    with tempfile.TemporaryDirectory(prefix='.extract-v07-', dir=runtime.parent) as temporary:
        staged = Path(temporary) / prefix
        staged.mkdir(mode=0o755)
        with tarfile.open(archive, mode='r:gz') as tf:
            for spec in layout:
                if spec['kind'] != 'file':
                    continue
                destination = staged / PurePosixPath(spec['name']).name
                stream = tf.extractfile(spec['name'])
                if stream is None:
                    raise ValueError('Missing ordinary tar stream')
                count = 0
                with stream, destination.open('xb') as output:
                    while True:
                        block = stream.read(min(BLOCK_BYTES, spec['size'] - count + 1))
                        if not block:
                            break
                        count += len(block)
                        if count > spec['size']:
                            raise ValueError('Extracted file exceeded pinned size')
                        output.write(block)
                if count != spec['size']:
                    raise ValueError('Extracted file shorter than pinned size')
                destination.chmod(spec['mode'])
        # Links are created only after all ordinary files. No archive path is
        # passed to extractall and no file write traverses an archive link.
        for spec in layout:
            if spec['kind'] == 'symlink':
                (staged / PurePosixPath(spec['name']).name).symlink_to(spec['linkname'])
        inspect_runtime(staged, config)
        if runtime.exists() or runtime.is_symlink():
            raise ValueError('Runtime appeared during extraction; refusing replacement')
        staged.rename(runtime)


def run(asset_root: Path, *, verify_only: bool) -> dict:
    config = _load_config()
    if asset_root.is_symlink():
        raise ValueError('Asset root must not be a symlink')
    if not verify_only:
        asset_root.mkdir(parents=True, exist_ok=True)
    if not asset_root.is_dir():
        raise ValueError('Asset directory missing; verification performs no downloads')
    # Inspect all existing content before any network operation or new write.
    for spec in config['assets']:
        path = asset_root / spec['relative_path']
        if path.exists() or path.is_symlink():
            verify_asset(path, spec)
        elif verify_only:
            raise ValueError(f'Asset missing in verification mode: {path.name}')
    runtime = asset_root / config['runtime_directory']
    if runtime.exists() or runtime.is_symlink():
        inspect_runtime(runtime, config)
    elif verify_only:
        raise ValueError('Extracted runtime missing in verification mode')
    if not verify_only:
        for spec in config['assets']:
            download_asset(asset_root, spec)
    archive = asset_root / 'llama-b11146-bin-ubuntu-x64.tar.gz'
    layout = inspect_archive(archive, config)
    if not verify_only:
        extract_runtime(asset_root, archive, config)
    verified = backend.verify_assets(asset_root)
    return {'schema_version': 'local_backend_bootstrap_verification_v0.7',
            'status': 'verified', 'verify_only': verify_only,
            'config_sha256': backend.digest(CONFIG), 'assets': len(verified['assets']),
            'asset_bytes': sum(s['bytes'] for s in config['assets']),
            'runtime_files': len(verified['runtime_files']), 'archive_members': len(layout),
            'runtime_files_sha256': verified['runtime_files_sha256'],
            'runtime_executable_sha256': verified['runtime_executable_sha256'],
            'model_launched': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, default=DEFAULT_ASSETS)
    parser.add_argument('--verify-only', action='store_true', help='Read-only verification; no network, downloads, extraction or model launch')
    args = parser.parse_args()
    print(json.dumps(run(args.assets, verify_only=args.verify_only), sort_keys=True))


if __name__ == '__main__':
    main()
