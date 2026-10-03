#!/usr/bin/env python3
"""Fetch the single selected public v14 weight; preserve prior asset provenance."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import fetch_local_backend_v07 as fetch
import local_backend_v07 as base

ROOT = Path(__file__).resolve().parents[1]
SPEC = {
    'relative_path': 'Qwen3.5-4B-Q5_K_M.gguf',
    'bytes': 3143656608,
    'sha256': '8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39',
    'url': 'https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q5_K_M.gguf',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--evict-pinned-v09-weight', type=Path)
    a = p.parse_args()
    assert not a.receipt.exists(), 'Preserve previous attempts'
    assert not a.directory.resolve().is_relative_to(ROOT.parent)
    a.directory.mkdir(parents=True, exist_ok=True)
    receipt = {'schema_version': 'reader_asset_v14', 'started_utc': datetime.now(timezone.utc).isoformat(),
               'asset': SPEC, 'quantizer': 'unsloth', 'upstream_model': 'Qwen/Qwen3.5-4B',
               'status': 'started', 'paid_services_used': False,
               'disk_free_before': shutil.disk_usage(a.directory).free}
    base.write_json(a.receipt, receipt)
    try:
        if a.evict_pinned_v09_weight:
            old = json.loads((ROOT / 'configs/model_backend_v09.json').read_text())['assets'][0]
            path = a.evict_pinned_v09_weight.resolve()
            assert not path.is_relative_to(ROOT.parent) and path.name == old['relative_path']
            fetch.verify_asset(path, old)
            receipt['reproducible_cache_eviction'] = {'path': str(path), 'asset': old,
                'verified_before_removal': True, 'reason': 'Disk capacity for new reader weight',
                'prior_outputs_and_runtime_preserved': True,
                'reproduction_note': 'Re-download exact pinned v09 weight before rerunning old inference.'}
            base.write_json(a.receipt, receipt)
            path.unlink()
        assert shutil.disk_usage(a.directory).free > SPEC['bytes'] + 250_000_000
        fetch.download_asset(a.directory, SPEC)
        fetch.verify_asset(a.directory / SPEC['relative_path'], SPEC)
        receipt.update(status='completed', verified_sha256=base.digest(a.directory / SPEC['relative_path']))
    except BaseException as e:
        receipt.update(status='failed', error_type=type(e).__name__, error=str(e))
        raise
    finally:
        receipt.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                       disk_free_after=shutil.disk_usage(a.directory).free)
        base.write_json(a.receipt, receipt)
    print(json.dumps({'status': receipt['status'], 'asset': SPEC['relative_path'], 'bytes': SPEC['bytes']}))


if __name__ == '__main__':
    main()
