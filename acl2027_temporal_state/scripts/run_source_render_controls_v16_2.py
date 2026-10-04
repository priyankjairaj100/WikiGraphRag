#!/usr/bin/env python3
"""Run unchanged authored Unicode and page-overflow controls after protocol freeze."""
import argparse
import json
from pathlib import Path
import render_source_blocks_lo_v16_2 as lo


def prepare_inputs(output):
    out = Path(output).resolve()
    if out == lo.ROOT.parent or lo.ROOT.parent in out.parents:
        raise ValueError('authored_input_output_must_be_external')
    fixture = lo.load_module(lo.ROOT / 'tests/test_source_render_v16.py', 'authored_control_fixture')
    controls = {'unicode.source.xml': fixture.FIXTURE.replace(b'Example margin', 'Caf\u00e9 margin \u00a3'.encode()),
                'overflow.source.xml': fixture.FIXTURE.replace(b'Example revenue', b'Long authored cell ' * 1800)}
    parent = json.loads((lo.ROOT / 'results/source_render_pipeline_preflight_v16_1.json').read_text())
    if any(lo.source_render.sha(raw) != parent['control_source_bindings'][name]
           for name, raw in controls.items()):
        raise ValueError('recreated_controls_do_not_match_original_inputs')
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in controls.items():
        lo.source_render.write_new(out / name, raw)


def execute(protocol_path, input_directory, external_output, public_output):
    protocol = json.loads(Path(protocol_path).read_text())
    out, public = Path(external_output).resolve(), Path(public_output).resolve()
    if (out == lo.ROOT.parent or lo.ROOT.parent in out.parents or out.exists() or public.exists()
            or public == out or out in public.parents):
        raise ValueError('unsafe_or_existing_output')
    for group in ['code_bindings', 'input_bindings']:
        for path, expected in protocol[group].items():
            if lo.source_render.digest(lo.ROOT / path) != expected:
                raise ValueError('binding_mismatch')
    for path, expected in protocol['runtime_bindings'].items():
        if lo.source_render.digest(path) != expected:
            raise ValueError('runtime_binding_mismatch')
    sources = []
    for control in protocol['controls']:
        source = Path(input_directory) / control['filename']
        if source.stat().st_size != control['bytes'] or lo.source_render.digest(source) != control['sha256']:
            raise ValueError('control_identity_mismatch')
        sources.append(source)
    out.mkdir(parents=True)
    fixture = lo.load_module(lo.ROOT / 'tests/test_source_render_v16.py', 'authored_fixture_selection')
    result = {'schema_version': 'source_render_authored_controls_v16_2',
              'protocol_sha256': lo.source_render.digest(protocol_path),
              'natural_sources_inspected': 0, 'started_at_utc': lo.source_render.utc(),
              'requested_controls': [c['name'] for c in protocol['controls']], 'controls': []}
    lo.write_json(out / 'start.json', result)
    baseline_error = None
    try:
        lo.RUN_OOM_BASELINE = lo.guard.read_snapshot()
    except Exception as error:
        baseline_error = type(error).__name__
        lo.write_json(out / 'baseline_private_exception.json',
                      {'error_type': type(error).__name__, 'message': str(error)})
    for control, source in zip(protocol['controls'], sources):
        entry, passed = {}, False
        try:
            if baseline_error:
                entry = {'status': 'baseline_telemetry_exception', 'error_type': baseline_error}
            else:
                raw = source.read_bytes()
                request = fixture.selection(raw)
                block = lo.source_render.prepare_blocks(raw, request)[0]
                entry = lo.render_block(block, out / control['name'], out, control['max_pages'])
                passed = entry['status'] == control['expected_status']
                expected_unicode = control.get('expected_unicode')
                if expected_unicode and entry.get('pdf'):
                    extracted = Path(entry['pdf']['path']).with_suffix('.txt').read_text()
                    entry['expected_unicode_retained'] = expected_unicode in extracted
                    passed = passed and entry['expected_unicode_retained']
                if control.get('expected_pages') is not None:
                    passed = passed and entry.get('pages') == control['expected_pages']
        except Exception as error:
            passed = False
            entry.update(status='control_exception_retained', error_type=type(error).__name__)
            lo.write_json(out / (control['name'] + '.private_exception.json'),
                          {'error_type': type(error).__name__, 'message': str(error)})
        entry['control_name'] = control['name']
        entry['control_passed'] = bool(passed)
        lo.write_json(out / (control['name'] + '.receipt.json'), entry)
        result['controls'].append(entry)
    result['finished_at_utc'] = lo.source_render.utc()
    result['status'] = 'authored_controls_passed_pending_unicode_visual_review' if all(
        c['control_passed'] for c in result['controls']) else 'authored_control_failure_retained'
    lo.write_json(out / 'receipt.json', result)
    public.parent.mkdir(parents=True, exist_ok=True)
    lo.write_json(public, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-inputs')
    parser.add_argument('--protocol')
    parser.add_argument('--inputs')
    parser.add_argument('--external-output')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.prepare_inputs:
        prepare_inputs(args.prepare_inputs)
        print(json.dumps({'status': 'unchanged_authored_controls_recreated'}))
        raise SystemExit(0)
    if not all([args.protocol, args.inputs, args.external_output, args.output]):
        parser.error('protocol, inputs, external-output and output are required for execution')
    r = execute(args.protocol, args.inputs, args.external_output, args.output)
    print(json.dumps({'status': r['status'], 'controls': [dict(name=c['control_name'], status=c['status'],
                      passed=c['control_passed']) for c in r['controls']]}))
