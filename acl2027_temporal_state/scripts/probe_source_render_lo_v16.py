#!/usr/bin/env python3
"""Separate LibreOffice feasibility candidate on the unchanged authored fixture."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = Path('/opt/codex/runtimes/codex-primary-runtime/dependencies/bin/override/soffice')
MEMORY_CAP = int(7.25 * 1024 ** 3)
TIMEOUT_SECONDS = 300
FIXTURE_SHA256 = '633dee7e51748f704700ac27299b1266ef615b69051724d7b163e65717fd390c'
ASSEMBLED_HTML_SHA256 = 'f6f409bc271a2b4120a46fe9b31ab921c2c1a93aafbd6f6f3aa7a769594a7b82'


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source_render = load_module(ROOT / 'scripts/render_source_blocks_v16.py', 'story_source_assembly')


def memory_bytes():
    return int(Path('/sys/fs/cgroup/memory.current').read_text().strip())


def file_record(path):
    path = Path(path)
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': source_render.digest(path)}


def write_json(path, data):
    source_render.write_new(path, source_render.encode(data))


def stop_group(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def monitored(command, output, *, timeout=TIMEOUT_SECONDS, cap=MEMORY_CAP, env=None):
    """Refuse above-cap starts and stop only this new process group if a cap trips."""
    baseline = memory_bytes()
    record = {'command': [str(x) for x in command], 'baseline_cgroup_bytes': baseline,
              'peak_sampled_cgroup_bytes': baseline, 'memory_cap_bytes': cap,
              'wall_timeout_seconds': timeout, 'poll_interval_seconds': 0.25}
    if baseline >= cap:
        return dict(record, status='blocked_before_launch_memory_cap', process_started=False)
    start = time.monotonic()
    with (output / 'stdout.txt').open('xb') as stdout, (output / 'stderr.txt').open('xb') as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                   env=env, start_new_session=True)
        record['process_started'] = True
        try:
            while process.poll() is None:
                used = memory_bytes()
                record['peak_sampled_cgroup_bytes'] = max(record['peak_sampled_cgroup_bytes'], used)
                if used >= cap:
                    record['status'] = 'stopped_at_memory_cap'
                    stop_group(process)
                    break
                if time.monotonic() - start >= timeout:
                    record['status'] = 'stopped_at_wall_timeout'
                    stop_group(process)
                    break
                time.sleep(0.25)
        finally:
            stop_group(process)
        record['exit_code'] = process.returncode
    record.setdefault('status', 'process_finished' if process.returncode == 0 else 'process_failed')
    record['elapsed_seconds'] = time.monotonic() - start
    return record


def authored_text_geometry(pdf):
    """Diagnostics only; source table geometry still requires visual comparison."""
    import fitz
    terms = ['2030', '2029', '1,234', '987', '56', '45', 'Authored source table']
    matches = {term: [] for term in terms}
    with fitz.open(pdf) as doc:
        texts = []
        for page_index, page in enumerate(doc):
            texts.append(page.get_text())
            for term in terms:
                matches[term].extend({'page': page_index + 1, 'rect': list(rect)}
                                     for rect in page.search_for(term))
        pages = len(doc)
    source_render.write_new(Path(pdf).with_suffix('.txt'), '\n\f\n'.join(texts).encode())
    checks = {'all_authored_terms_present_once': all(len(v) == 1 for v in matches.values())}
    if checks['all_authored_terms_present_once']:
        center = lambda term: (matches[term][0]['rect'][0] + matches[term][0]['rect'][2]) / 2
        boundary = (center('2030') + center('2029')) / 2
        checks['two_year_headers_left_to_right'] = center('2030') < center('2029')
        checks['first_numeric_column_under_first_year'] = all(center(x) < boundary for x in ['1,234', '56'])
        checks['second_numeric_column_under_second_year'] = all(center(x) > boundary for x in ['987', '45'])
        checks['caption_above_headers'] = matches['Authored source table'][0]['rect'][3] <= min(
            matches[x][0]['rect'][1] for x in ['2030', '2029'])
    return {'pages': pages, 'matches': matches, 'checks': checks,
            'all_geometry_diagnostics_pass': all(checks.values()), 'visual_review_required': True}


def run_probe(external_output):
    output = Path(external_output).resolve()
    if output == ROOT.parent or ROOT.parent in output.parents:
        raise ValueError('authored_probe_output_must_be_outside_git')
    # This candidate cannot accept natural source paths or arbitrary HTML.
    fixture = load_module(ROOT / 'tests/test_source_render_v16.py', 'authored_source_fixture')
    selected = fixture.selection()
    prepared = source_render.prepare_blocks(fixture.FIXTURE, selected)
    if (len(prepared) != 1 or source_render.sha(fixture.FIXTURE) != FIXTURE_SHA256
            or source_render.sha(prepared[0]['html']) != ASSEMBLED_HTML_SHA256):
        raise ValueError('exact_pinned_authored_fixture_and_assembly_required')
    output.mkdir(parents=True, exist_ok=False)
    profile, temporary, rendered = output / 'profile', output / 'tmp', output / 'rendered'
    for folder in [profile, temporary, rendered]:
        folder.mkdir()
    source_render.write_new(output / 'authored-source.xml', fixture.FIXTURE)
    source_render.write_new(output / 'authored-block.html', prepared[0]['html'])
    write_json(output / 'selection.json', selected)
    assembly = {k: v for k, v in prepared[0].items() if k not in ['fragment', 'html']}
    record = {'schema_version': 'authored_libreoffice_render_probe_v16',
              'candidate': 'libreoffice_html_import_separate_from_failed_story_candidate',
              'started_at_utc': source_render.utc(), 'natural_sources_inspected': 0,
              'source_sha256': source_render.sha(fixture.FIXTURE),
              'assembly': assembly, 'input_html': file_record(output / 'authored-block.html'),
              'source_fixture': file_record(output / 'authored-source.xml'),
              'wrapper': file_record(WRAPPER),
              'code': [file_record(Path(__file__)), file_record(ROOT / 'scripts/render_source_blocks_v16.py'),
                       file_record(ROOT / 'tests/test_source_render_v16.py')],
              'resource_policy': 'unchanged_authored_fixture_contains_no_subresource_or_script_in_assembled_HTML',
              'natural_source_resource_policy_validated': False,
              'limitations': ['Office HTML importer is not a browser CSS engine.',
                              'Same original markup and source styles are retained; unsupported CSS is not repaired.',
                              'No reconstruction from normalized values or label substitution.',
                              'Fixture success alone cannot establish natural financial scope.',
                              'Memory cap uses sampled cgroup readings, not a kernel-enforced process limit.']}
    write_json(output / 'start.json', record)
    command = [str(WRAPPER), '-env:UserInstallation=' + profile.as_uri(), '--headless',
               '--nologo', '--nodefault', '--norestore', '--convert-to', 'pdf',
               '--outdir', str(rendered), str(output / 'authored-block.html')]
    env = os.environ.copy()
    env['TMPDIR'] = str(temporary)
    env['SAL_USE_VCLPLUGIN'] = 'svp'
    record['conversion'] = monitored(command, output, env=env)
    pdf = rendered / 'authored-block.pdf'
    if record['conversion']['status'] != 'process_finished':
        record['status'] = record['conversion']['status']
    elif not pdf.is_file():
        record['status'] = 'no_pdf_produced'
    else:
        record['pdf'] = file_record(pdf)
        record['text_geometry'] = authored_text_geometry(pdf)
        raster = output / 'raster'
        raster.mkdir()
        record['rasterization'] = monitored(['pdftoppm', '-r', '110', '-png', str(pdf),
                                            str(raster / 'page')], raster,
                                           timeout=60, env=env)
        record['pngs'] = [file_record(p) for p in sorted(raster.glob('page-*.png'))]
        record['status'] = ('rendered_pending_visual_review'
                            if record['rasterization']['status'] == 'process_finished'
                            else 'rasterization_failed_or_blocked')
    record['finished_at_utc'] = source_render.utc()
    write_json(output / 'receipt.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-output', required=True)
    args = parser.parse_args()
    record = run_probe(args.external_output)
    print(json.dumps({'status': record['status'], 'receipt': str(Path(args.external_output) / 'receipt.json')}))
    return 0 if record['status'] == 'rendered_pending_visual_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())
