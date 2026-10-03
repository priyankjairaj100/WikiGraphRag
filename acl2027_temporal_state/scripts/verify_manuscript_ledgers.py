#!/usr/bin/env python3
"""Check manuscript artifact bindings; not paper correctness or layout."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    ledgers = []
    for version in ['v07', 'v08', 'v09']:
        path = ROOT / 'paper' / ('claim_ledger_' + version + '.json')
        ledger = json.loads(path.read_bytes())
        refs = [{'path': ledger['draft'], 'sha256': ledger['draft_sha256']}]
        for claim in ledger['claims']:
            refs.extend(claim['artifacts'])
        for ref in refs:
            rel = Path(ref['path'])
            assert not rel.is_absolute() and '..' not in rel.parts
            assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == ref['sha256'], ref['path']
        ledgers.append({'version': version, 'claims': len(ledger['claims']), 'hash_references_checked': len(refs)})
    out = {'status': 'passed_artifact_bindings', 'ledgers': ledgers,
           'hash_references_checked': sum(r['hash_references_checked'] for r in ledgers),
           'semantic_claim_validation': False, 'compiled_pdf': False, 'visual_layout_verified': False}
    (ROOT / 'results/manuscript_binding_review_v09.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out))


if __name__ == '__main__':
    main()
