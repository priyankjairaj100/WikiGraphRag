#!/usr/bin/env python3
"""Cost all source bundles with the pinned reader's native tokenizer; no answers."""
import argparse
import json
from pathlib import Path

import native_structured_reader_v14 as native
import retrieve_structured_v14 as retrieval

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reader-config', type=Path, required=True)
    p.add_argument('--tokenizer-artifacts', type=Path, required=True)
    p.add_argument('--contexts', type=Path, required=True)
    p.add_argument('--metadata', type=Path, required=True)
    p.add_argument('--manifest', type=Path, default=ROOT / 'data/structured_reader_v14/corpus_manifest.json')
    a = p.parse_args()
    with native.tokenizer_session(a.reader_config, a.tokenizer_artifacts) as bridge:
        result = retrieval.retrieve(a.manifest, ROOT / 'data/paired_reader_v13/questions.json',
                                    Path(native.__file__), a.reader_config,
                                    a.contexts, a.metadata, counter=bridge.count_prompt)
    print(json.dumps({k: result[k] for k in ('question_count', 'context_count',
                                           'conditions', 'external_contexts_sha256')}))


if __name__ == '__main__':
    main()
