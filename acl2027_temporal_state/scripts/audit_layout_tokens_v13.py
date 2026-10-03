"""Vocabulary-only sizing of layout prompts; no forward passes or answers."""
from pathlib import Path
import hashlib
import json
import verify_larger_scorer_external_v09 as prior

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path('/workspace/scratch/bdef663e3dfc/tmp/paired_reader_v13')
ASSETS = Path('/workspace/scratch/bdef663e3dfc/tmp/local_backend_v09')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    original_path = EXTERNAL / 'main_requests.json'
    layout_path = EXTERNAL / 'layout_requests.json'
    rendered_path = EXTERNAL / 'main_attempt01/rendered_prompts.json'
    original = json.loads(original_path.read_text())
    layout = json.loads(layout_path.read_text())
    rendered = json.loads(rendered_path.read_text())
    assert len(original) == len(layout) == len(rendered) == 16
    records = []
    for old, new, native in zip(original, layout, rendered):
        assert old['request_id'] == new['request_id'] == native['request_id']
        prompt = native['prompt']['rendered_prompt']
        assert prompt.count(old['prompt']) == 1
        old_tokens, old_trace = prior.tokenize(ASSETS, 'large', prompt)
        assert old_tokens == native['prompt']['input_token_ids']
        changed = prompt.replace(old['prompt'], new['prompt'], 1)
        new_tokens, new_trace = prior.tokenize(ASSETS, 'large', changed)
        records.append({'request_id': old['request_id'], 'original_tokens': len(old_tokens),
                        'layout_tokens': len(new_tokens), 'within_3500_input_cap': len(new_tokens) <= 3500,
                        'within_4096_with_256_reserve': len(new_tokens) + 256 <= 4096,
                        'candidate_render_sha256': hashlib.sha256(changed.encode()).hexdigest(),
                        'layout_token_ids_sha256': prior.scorer.base.canonical_hash(new_tokens),
                        'original_trace': old_trace, 'layout_trace': new_trace})
    report = {'schema_version': 'layout_token_audit_v0.13',
              'script_sha256': digest(Path(__file__)),
              'helper_sha256': digest(ROOT / 'scripts/verify_larger_scorer_external_v09.py'),
              'main_requests_sha256': digest(original_path), 'layout_requests_sha256': digest(layout_path),
              'main_native_preflight_sha256': digest(rendered_path),
              'records': records, 'tokenizer_processes': 32, 'new_forward_pass_calls': 0,
              'all_original_tokens_match_native_preflight': True,
              'all_layout_inputs_fit': all(x['within_3500_input_cap'] for x in records),
              'model_answer_contents_accessed': False,
              'native_requirement': 'The actual layout batch must independently re-render and tokenize before any completion; this offline estimate is not a replacement.'}
    destination = ROOT / 'results/layout_token_audit_v13.json'
    assert not destination.exists()
    destination.write_text(json.dumps(report, indent=2) + '\n')
    return {'all_layout_inputs_fit': report['all_layout_inputs_fit'],
            'layout_input_range': [min(x['layout_tokens'] for x in records), max(x['layout_tokens'] for x in records)],
            'tokenizer_processes': 32, 'new_forward_pass_calls': 0}


if __name__ == '__main__':
    print(json.dumps(audit()))
