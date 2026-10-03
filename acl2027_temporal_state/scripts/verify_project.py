"""Run the bounded local verification suite and record its outcome."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    commands = [
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        [sys.executable, 'scripts/run_diagnostics.py'],
        [sys.executable, 'scripts/run_decoder_diagnostics.py'],
        [sys.executable, 'scripts/run_natural_pilot.py'],
        [sys.executable, 'scripts/run_representation_pilot.py'],
        [sys.executable, 'scripts/run_coupled_diagnostics.py'],
        [sys.executable, 'scripts/run_natural_cache_smoke.py'],
        [sys.executable, 'scripts/audit_coupled_finite.py'],
        [sys.executable, 'scripts/run_correction_diagnostics.py'],
        [sys.executable, 'scripts/run_correction_source_demo.py'],
        [sys.executable, 'scripts/audit_correction_finite.py'],
        [sys.executable, 'scripts/run_objective_diagnostics_v06.py'],
        [sys.executable, 'scripts/audit_objectives_v06.py'],
        [sys.executable, 'scripts/run_natural_model_pilot_v06.py',
         '--candidates', 'data/natural_model_pilot_v06/candidate_output_repaired.json',
         '--candidate-receipt', 'data/natural_model_pilot_v06/candidate_repair_receipt.json',
         '--candidate-prompt', 'configs/candidate_prompt_v06_repair.txt',
         '--candidate-schema', 'configs/candidate_schema_v06_repair.json',
         '--repair-protocol', 'data/natural_model_pilot_v06/validation_repair_protocol.json',
         '--original-candidates', 'data/natural_model_pilot_v06/candidate_output_raw.json',
         '--original-candidate-receipt', 'data/natural_model_pilot_v06/candidate_run_receipt.json',
         '--lineage-input', 'data/natural_model_pilot_v06/candidate_validation_first_pass.json'],
        [sys.executable, 'scripts/run_ordinal_sensitivity_v06.py'],
        [sys.executable, 'scripts/validate_candidate_ambiguity_v07.py',
         '--raw-output', 'data/natural_model_pilot_v07/candidate_ambiguity_audit_raw.json'],
        [sys.executable, 'scripts/run_likelihood_pilot_v07.py'],
        [sys.executable, 'scripts/replay_source_stream_v08.py'],
        [sys.executable, 'scripts/scorer_controls_v08.py'],
        [sys.executable, 'scripts/review_scorer_controls_v08.py'],
        [sys.executable, 'scripts/verify_source_capture_v09.py'],
        [sys.executable, 'scripts/source_stream_v09.py', 'replay'],
        [sys.executable, 'scripts/larger_scorer_v09.py', 'verify'],
        [sys.executable, 'scripts/audit_larger_scorer_v09.py'],
        [sys.executable, 'scripts/analyze_challenge_v09.py'],
        [sys.executable, 'scripts/verify_manuscript_ledgers.py'],
        [sys.executable, 'scripts/prepare_model_pilot.py', '--dry-run'],
        [sys.executable, 'scripts/audit_original.py'],
    ]
    runs = []
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        runs.append({'command': command[1:], 'returncode': result.returncode,
                     'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            break
    report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'all_passed': len(runs) == len(commands) and all(r['returncode'] == 0 for r in runs),
              'runs': runs}
    count = re.search(r'Ran (\d+) tests', runs[0]['stderr'])
    report['unit_tests_passed'] = int(count[1]) if count and runs[0]['returncode'] == 0 else 0
    out = ROOT / 'results' / 'verification.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ['all_passed', 'unit_tests_passed']}, indent=2))
    if not report['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
