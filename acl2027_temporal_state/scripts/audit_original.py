"""Reproduce the original restatement/component failure from the preserved ZIP.

This is a targeted code regression, not a model comparison. Gold values are
supplied to the original function because its submitted interval API reads them;
that behavior is part of the audited implementation, not the new inference path.
"""
from pathlib import Path
import hashlib
import json
import sys
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'reference' / 'original_submission.zip'
sys.path.insert(0, str(ARCHIVE) + '/wikigraphrag-code-and-data')

from wikigraphrag.core.claim import Claim
from wikigraphrag.detect import SupersessionDetector
from wikigraphrag.lifecycle.intervals import derive_timelines, as_of


def main():
    claims = [
        Claim('c1', 'The CEO of Acme is Alice Smith.', 2000.0, value='Alice Smith'),
        Claim('c2', 'The CEO of Acme is Bob Jones.', 2001.0, value='Bob Jones'),
        Claim('c3', 'The CEO of Acme is Bob Jones.', 2002.0, value='Bob Jones'),
    ]
    edges = SupersessionDetector().detect(claims)
    timelines = derive_timelines(claims, edges)
    anchored = next(t for t in timelines.values() if any(i.cid == 'c1' for i in t))
    hit = as_of(anchored, 2001.5)
    result = {
        'evaluation_kind': 'targeted_original_code_audit',
        'archive_sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        'fixture': 'A at 2000; B at 2001; B restated at 2002',
        'edges': [asdict(e) for e in edges],
        'components': [[i.cid for i in t] for t in timelines.values()],
        'query_time': 2001.5,
        'component_anchor': 'c1 (explicit diagnostic anchor, not natural query routing)',
        'expected': 'Bob Jones',
        'actual': hit.value if hit else None,
        'failure_reproduced': hit is not None and hit.value == 'Alice Smith',
        'limitations': ['Constructed regression only', 'Original interval function reads Claim.value',
                        'Does not measure end-to-end natural question answering'],
    }
    out = ROOT / 'results' / 'original_restatement_audit.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['failure_reproduced']:
        raise SystemExit('Expected original failure was not reproduced; inspect inputs')


if __name__ == '__main__':
    main()
