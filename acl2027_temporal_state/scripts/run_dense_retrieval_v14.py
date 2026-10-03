#!/usr/bin/env python3
"""Pack all 48 dense baseline contexts with one native tokenizer; no completions."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import dense_retrieval_v14 as retrieval
import native_structured_reader_v14 as native

ROOT = Path(__file__).resolve().parents[1]


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'configs/dense_retriever_candidate_v14.json')
    parser.add_argument('--manifest', type=Path, default=ROOT/'data/structured_reader_v14/corpus_manifest.json')
    parser.add_argument('--questions', type=Path, default=ROOT/'data/paired_reader_v13/questions.json')
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--reader-config', type=Path, required=True)
    parser.add_argument('--tokenizer-artifacts', type=Path, required=True)
    parser.add_argument('--contexts', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--runtime-receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.runtime_receipt.exists():
        raise FileExistsError('new wrapper runtime receipt required')
    started, clock = time.time(), time.monotonic()
    receipt = {'schema_version': 'dense_native_packing_runtime_v0.14', 'started_unix_seconds': started,
               'wrapper_sha256': sha(__file__), 'encoder_retriever_sha256': sha(retrieval.__file__),
               'native_adapter_sha256': sha(native.__file__), 'reader_config_sha256': sha(args.reader_config),
               'native_completion_calls': 0, 'encoder_loaded': False, 'native_tokenizer_sessions': 1}
    try:
        with native.tokenizer_session(args.reader_config, args.tokenizer_artifacts) as bridge:
            result = retrieval.retrieve(args.manifest, args.questions, args.config, args.index,
                                        Path(native.__file__), args.reader_config, args.contexts, args.metadata,
                                        counter=bridge.count_prompt)
        receipt.update(status='complete', context_count=result['context_count'], metadata_sha256=sha(args.metadata),
                       external_contexts_sha256=result['external_contexts_sha256'], inner_timings=result['timings'])
        print(json.dumps({k: result[k] for k in ('question_count', 'context_count', 'external_contexts_sha256')}))
    except Exception as error:
        receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        receipt.update(finished_unix_seconds=time.time(), elapsed_seconds_including_native_session=time.monotonic()-clock)
        retrieval.write_json_new(args.runtime_receipt, receipt)


if __name__ == '__main__': main()
