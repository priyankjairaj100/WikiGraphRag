#!/usr/bin/env python3
"""Frozen source-block LO inspection; all blocks remain pending manual admission."""
from __future__ import annotations
import argparse
import gc
import json
import os
from pathlib import Path
import re
import subprocess
import time

from probe_source_render_lo_v16 import ROOT, WRAPPER, MEMORY_CAP, source_render, load_module
from probe_source_render_lo_v16 import file_record, write_json
import signal
import cgroup_guard_v16_2 as guard
import process_group_rss_v16 as rss_guard

SCHEMA = 'source_block_lo_inspection_v16_2'
LO_RESERVE_BYTES = 512 * 1024 ** 2
LO_GROUP_RSS_LIMIT_BYTES = 512 * 1024 ** 2
RUN_OOM_BASELINE = None
MAX_BYTES = 150_000_000
MAX_BLOCK_BYTES = 20_000_000
RECEIPT_RESERVE = 4_000_000
IX = '{http://www.xbrl.org/2013/inlineXBRL}'
PASSIVE_XHTML = set('html head body style table thead tbody tfoot tr td th caption col colgroup '
                    'p div span b strong i em small font u s strike br hr sub sup a h1 h2 h3 h4 h5 h6 '
                    'center nobr pre code blockquote ol ul li dl dt dd section article'.split())
PASSIVE_IX = {'nonFraction', 'nonNumeric', 'continuation', 'exclude', 'hidden'}
CSS_RISKS = ['display', 'visibility', 'position', 'z-index', 'opacity', 'overflow', 'clip',
             'transform', 'direction', 'writing-mode', 'order', 'flex', 'grid', 'content']
PASSIVE_CSS_FUNCTIONS = {'rgb', 'rgba', 'hsl', 'hsla', 'calc', 'min', 'max', 'clamp', 'var'}


def resource_guard(html):
    """Conservative refusal profile, not modification/sanitization of source."""
    nodes = source_render.byte_reader._parse(html)
    issues, css = [], []
    # XML processing instructions are not element nodes. Also refuse CDATA
    # containing markup that an HTML importer could parse differently from XML.
    if b'<?' in html:
        issues.append('processing_instruction_outside_passive_profile')
    if any(b'<' in part for part in re.findall(rb'<!\[CDATA\[(.*?)\]\]>', html, re.S)):
        issues.append('cdata_markup_parser_difference_outside_profile')
    for n in nodes:
        local = n.tag.split('}', 1)[-1]
        if not ((n.tag.startswith(source_render.XH) and local in PASSIVE_XHTML)
                or (n.tag.startswith(IX) and local in PASSIVE_IX)):
            issues.append('element_outside_passive_profile')
        if n.tag == source_render.XH + 'style':
            css.append(n.text())
        for name, value in n.attrs.items():
            local_name = name.split('}', 1)[-1].lower()
            if local_name.startswith('on'):
                issues.append('event_attribute_forbidden')
            if local_name in {'src', 'srcset', 'data', 'background', 'poster', 'codebase',
                              'classid', 'action', 'formaction', 'srcdoc', 'archive'}:
                issues.append('resource_or_active_attribute_forbidden')
            if local_name == 'href' and (n.tag != source_render.XH + 'a' or not value.startswith('#')):
                issues.append('non_fragment_href_forbidden')
            if local_name == 'style':
                css.append(value)
    for value in css:
        clean = re.sub(r'/\*.*?\*/', '', value, flags=re.S).lower()
        # All at-rules and CSS escapes are outside this deliberately narrow profile.
        if '\\' in value or '@' in value or re.search(r'url\s*\(', clean):
            issues.append('css_resource_or_escape_or_at_rule_outside_profile')
        if re.search(r'(?:expression\s*\(|behavior\s*:|-moz-binding\s*:)', clean):
            issues.append('active_css_forbidden')
        functions = re.findall(r'(?<![-\w])([-a-zA-Z][-\w]*)\s*\(', clean)
        if any(name not in PASSIVE_CSS_FUNCTIONS for name in functions):
            issues.append('css_function_outside_passive_profile')
    risk_names = sorted({name for name in CSS_RISKS for value in css
                         if re.search(r'(?<![-\w])' + re.escape(name) + r'\s*:', value, re.I)})
    return {'status': 'refused' if issues else 'passive_resource_profile_passed',
            'issues': sorted(set(issues)), 'css_property_review_flags': risk_names,
            'full_css_manual_review_required': True, 'css_blocks_and_attributes': len(css),
            'policy': 'refuse_sources_with_active_or_loadable_resources_no_markup_rewrite',
            'network_system_call_audited': False}


def byte_size(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob('*') if p.is_file())


def bind_owned_group(pgid):
    return rss_guard.bind_group(pgid)


def owned_group_rss(pgid, anchor):
    return rss_guard.read_group_rss(pgid, anchor=anchor)


def stop_owned_group(process):
    # This process was created with start_new_session=True. The leader may have
    # exited while a child still owns the same group, so do not return on poll().
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.poll()
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError('owned_process_group_cleanup_timeout')


def run_command(command, logs, attempt, *, timeout, env):
    global RUN_OOM_BASELINE
    baseline = guard.read_snapshot()
    if RUN_OOM_BASELINE is None:
        RUN_OOM_BASELINE = baseline
    info = {'baseline_snapshot': baseline, 'run_oom_baseline': RUN_OOM_BASELINE,
            'peak_sampled_actual_total_bytes': baseline.get('memory_current_bytes'),
            'peak_sampled_pressure_proxy_bytes': baseline.get('pressure_proxy_bytes'),
            'peak_sampled_group_rss_bytes': 0, 'worker_reserve_bytes': LO_RESERVE_BYTES,
            'group_rss_limit_bytes': LO_GROUP_RSS_LIMIT_BYTES,
            'timeout_seconds': timeout, 'attempt_output_cap_bytes': MAX_BYTES,
            'block_output_cap_bytes': MAX_BLOCK_BYTES, 'resource_policy': 'cache_aware_v16_2',
            'telemetry_samples': [baseline], 'process_group_samples': []}
    reason = guard.policy_reason(baseline, baseline=RUN_OOM_BASELINE,
                                 reserve_bytes=LO_RESERVE_BYTES)
    if reason:
        return dict(info, status='blocked_before_launch_' + reason, process_started=False)
    if byte_size(attempt) >= MAX_BYTES:
        return dict(info, status='blocked_before_launch_output_cap', process_started=False)
    start = time.monotonic()
    with (logs / 'stdout.txt').open('xb') as stdout, (logs / 'stderr.txt').open('xb') as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env, start_new_session=True)
        info['process_started'] = True
        try:
            anchor = bind_owned_group(process.pid)
            info['ownership_anchor'] = anchor
            if not anchor.get('telemetry_ok'):
                info['status'] = 'stopped_at_owned_group_anchor_unavailable'
            while process.poll() is None:
                if 'status' in info:
                    break
                snapshot = guard.read_snapshot()
                info['telemetry_samples'].append(snapshot)
                reason = guard.policy_reason(snapshot, baseline=RUN_OOM_BASELINE)
                if snapshot.get('telemetry_ok'):
                    info['peak_sampled_actual_total_bytes'] = max(info['peak_sampled_actual_total_bytes'], snapshot['memory_current_bytes'])
                    info['peak_sampled_pressure_proxy_bytes'] = max(info['peak_sampled_pressure_proxy_bytes'], snapshot['pressure_proxy_bytes'])
                if reason:
                    info['status'] = 'stopped_at_' + reason
                    break
                group = owned_group_rss(process.pid, anchor)
                info['process_group_samples'].append(group)
                if not group.get('telemetry_ok'):
                    info['status'] = 'stopped_at_process_group_telemetry_unavailable'
                    break
                if not group.get('group_members_observed') and process.poll() is None:
                    info['status'] = 'stopped_at_live_process_group_unobserved'
                    break
                rss = group['rss_bytes']
                info['peak_sampled_group_rss_bytes'] = max(info['peak_sampled_group_rss_bytes'], rss)
                if rss >= LO_GROUP_RSS_LIMIT_BYTES:
                    info['status'] = 'stopped_at_group_rss_limit'
                elif byte_size(attempt) >= MAX_BYTES or byte_size(logs.parent) >= MAX_BLOCK_BYTES:
                    info['status'] = 'stopped_at_output_cap'
                elif time.monotonic() - start >= timeout:
                    info['status'] = 'stopped_at_wall_timeout'
                if 'status' in info:
                    break
                time.sleep(0.25)
        finally:
            stop_owned_group(process)
    final = guard.read_snapshot()
    info['telemetry_samples'].append(final)
    info['final_snapshot'] = final
    if final.get('telemetry_ok'):
        info['peak_sampled_actual_total_bytes'] = max(info['peak_sampled_actual_total_bytes'], final['memory_current_bytes'])
        info['peak_sampled_pressure_proxy_bytes'] = max(info['peak_sampled_pressure_proxy_bytes'], final['pressure_proxy_bytes'])
    final_reason = guard.policy_reason(final, baseline=RUN_OOM_BASELINE)
    if final_reason:
        info.setdefault('status', 'failed_final_' + final_reason)
    info['exit_code'] = process.returncode
    info['elapsed_seconds'] = time.monotonic() - start
    info.setdefault('status', 'process_finished' if process.returncode == 0 else 'process_failed')
    return info


def render_block(item, folder, attempt, max_pages):
    entry = {k: v for k, v in item.items() if k not in {'html', 'fragment'}}
    entry['admission_status'] = 'not_admitted_pending_full_visual_and_css_review'
    if (byte_size(attempt) + 2 * len(item['html']) + len(item['fragment']) + 256
            >= MAX_BYTES - RECEIPT_RESERVE):
        return dict(entry, status='blocked_before_write_output_cap')
    folder.mkdir()
    entry['resource_check'] = resource_guard(item['html'])
    source_render.write_new(folder / 'source-fragment.xml', item['fragment'])
    source_render.write_new(folder / 'source-assembly.html', item['html'])
    if entry['resource_check']['status'] == 'refused':
        return dict(entry, status='unrenderable_resource_profile_refusal')
    # An encoding declaration is metadata, not source content or a missing label.
    # The exact pre-declaration assembly and original fragment remain separately saved.
    render_html = item['html'].replace(b'<head>', b'<head><meta http-equiv="Content-Type" '
                                       b'content="text/html; charset=utf-8"/>', 1)
    entry['encoding_metadata_added'] = 'UTF-8 Content-Type meta in generated head only'
    entry['source_assembly_sha256'] = source_render.sha(item['html'])
    entry['render_html_sha256'] = source_render.sha(render_html)
    source_render.write_new(folder / 'source-block.html', render_html)
    profile, temporary, rendered, logs = [folder / x for x in ['profile', 'tmp', 'rendered', 'conversion']]
    for p in [profile, temporary, rendered, logs]:
        p.mkdir()
    # One additional page detects overflow; page 9 is evidence of failure for cap 8.
    options = {'PageRange': {'type': 'string', 'value': '1-' + str(max_pages + 1)},
               'PDFViewSelection': {'type': 'long', 'value': '3'}}
    conversion_filter = 'pdf:writer_web_pdf_Export:' + json.dumps(options, separators=(',', ':'))
    command = [str(WRAPPER), '-env:UserInstallation=' + profile.as_uri(), '--headless',
               '--nologo', '--nodefault', '--norestore', '--convert-to', conversion_filter,
               '--outdir', str(rendered), str(folder / 'source-block.html')]
    env = os.environ.copy()
    env['TMPDIR'] = str(temporary)
    env['SAL_USE_VCLPLUGIN'] = 'svp'
    entry['conversion'] = run_command(command, logs, attempt, timeout=300, env=env)
    entry['pdf_filter'] = conversion_filter
    pdf = rendered / 'source-block.pdf'
    if entry['conversion']['status'] != 'process_finished':
        return dict(entry, status=entry['conversion']['status'])
    if not pdf.is_file():
        return dict(entry, status='no_pdf_produced')
    import fitz
    with fitz.open(pdf) as doc:
        entry['pages'] = len(doc)
        source_render.write_new(rendered / 'source-block.txt', '\n\f\n'.join(p.get_text() for p in doc).encode())
    if entry['pages'] > max_pages:
        return dict(entry, status='page_cap_exceeded_partial_pdf_retained')
    if byte_size(folder) >= MAX_BLOCK_BYTES or byte_size(attempt) >= MAX_BYTES:
        return dict(entry, status='output_cap_exceeded_partial_files_retained')
    raster = folder / 'raster'
    raster.mkdir()
    entry['rasterization'] = run_command(['pdftoppm', '-r', '110', '-png', str(pdf),
                                         str(raster / 'page')], raster, attempt, timeout=60, env=env)
    if entry['rasterization']['status'] != 'process_finished':
        return dict(entry, status='rasterization_failed_or_blocked')
    pngs = sorted(raster.glob('page-*.png'))
    if len(pngs) != entry['pages']:
        return dict(entry, status='raster_page_population_mismatch')
    if byte_size(folder) >= MAX_BLOCK_BYTES or byte_size(attempt) >= MAX_BYTES:
        return dict(entry, status='output_cap_exceeded_partial_files_retained')
    entry['pdf'] = file_record(pdf)
    entry['pngs'] = [file_record(p) for p in pngs]
    return dict(entry, status='rendered_pending_full_visual_and_css_review')


def execute(protocol_path, sources, external_output, public_output):
    protocol_path, sources = Path(protocol_path).resolve(), Path(sources).resolve()
    output, public = Path(external_output).resolve(), Path(public_output).resolve()
    if ROOT.parent == output or ROOT.parent in output.parents:
        raise ValueError('external_output_inside_git')
    if output == public or output in public.parents or public.exists() or output.exists():
        raise ValueError('invalid_or_existing_output')
    protocol = json.loads(protocol_path.read_text())
    if protocol.get('schema_version') != SCHEMA or not 1 <= protocol.get('max_pages', 0) <= 12:
        raise ValueError('protocol_schema_or_page_bound')
    required = {'scripts/render_source_blocks_lo_v16_2.py', 'scripts/cgroup_guard_v16_2.py',
                'scripts/process_group_rss_v16.py',
                'scripts/probe_source_render_lo_v16.py',
                'scripts/render_source_blocks_v16.py', 'src/temporal_state/typed_reader_v15_1.py'}
    if not required.issubset(protocol.get('code_bindings', {})):
        raise ValueError('required_code_bindings_missing')
    for group in ['code_bindings', 'input_bindings']:
        for rel, expected in protocol.get(group, {}).items():
            if source_render.digest(ROOT / rel) != expected:
                raise ValueError(group + '_hash_mismatch')
    if not protocol.get('runtime_bindings') or str(WRAPPER) not in protocol['runtime_bindings']:
        raise ValueError('runtime_bindings_missing')
    for path, expected in protocol['runtime_bindings'].items():
        if source_render.digest(path) != expected:
            raise ValueError('runtime_binding_mismatch')
    population = protocol.get('sources', [])
    if not population or len(population) > 12:
        raise ValueError('source_population_outside_profile')
    if len({s['source_path'] for s in population}) != len(population):
        raise ValueError('duplicate_source_identity')
    for source in population:
        path = (sources / source['external_filename']).resolve()
        if sources not in path.parents or path.stat().st_size != source['source_bytes'] \
                or source_render.digest(path) != source['source_sha256']:
            raise ValueError('source_preflight_mismatch')
    global RUN_OOM_BASELINE
    RUN_OOM_BASELINE = guard.read_snapshot()
    output.mkdir(parents=True)
    public.parent.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': SCHEMA, 'protocol_sha256': source_render.digest(protocol_path),
              'started_at_utc': source_render.utc(), 'sources': [], 'natural_QA_predictions': 0,
              'browser_fidelity_claimed': False, 'reference_admission_performed': False}
    write_json(output / 'start.json', result)
    for source_index, source in enumerate(population):
        item = {'source_path': source['source_path'], 'source_sha256': source['source_sha256'],
                'requested_blocks': len(source['blocks']), 'blocks': []}
        folder = output / f'source_{source_index:02d}'
        folder.mkdir()
        if not source['blocks']:
            item['status'] = 'no_candidates_in_frozen_pool'
            result['sources'].append(item)
            continue
        try:
            preparation_reason = guard.policy_reason(guard.read_snapshot(), baseline=RUN_OOM_BASELINE,
                                                     reserve_bytes=LO_RESERVE_BYTES)
            if preparation_reason:
                raise source_render.RenderError('source_preparation_' + preparation_reason)
            raw = (sources / source['external_filename']).read_bytes()
            prepared = source_render.prepare_blocks(raw, source)
            for block_index, block in enumerate(prepared):
                block_folder = folder / f'block_{block_index:02d}'
                try:
                    entry = render_block(block, block_folder, output, protocol['max_pages'])
                except Exception as error:
                    entry = {'block_id': block['block_id'], 'dom_path': block['dom_path'],
                             'anchor': block['anchor'], 'status': 'block_exception',
                             'error_type': type(error).__name__}
                    write_json(folder / f'block_{block_index:02d}_private_exception.json',
                               {'error_type': type(error).__name__, 'message': str(error)})
                write_json(folder / f'block_{block_index:02d}_receipt.json', entry)
                item['blocks'].append(entry)
            del prepared, raw
        except Exception as error:
            write_json(folder / 'private_exception.json', {'type': type(error).__name__, 'message': str(error)})
            item['source_error_type'] = type(error).__name__
            item['blocks'] = [dict(block_id=b['block_id'], dom_path=b['dom_path'], anchor=b['anchor'],
                                   status='source_preparation_failed') for b in source['blocks']]
        gc.collect()
        result['sources'].append(item)
    result['finished_at_utc'] = source_render.utc()
    result['requested_blocks'] = sum(s['requested_blocks'] for s in result['sources'])
    result['rendered_blocks'] = sum(b['status'] == 'rendered_pending_full_visual_and_css_review'
                                    for s in result['sources'] for b in s['blocks'])
    result['retained_failed_or_unrenderable_blocks'] = result['requested_blocks'] - result['rendered_blocks']
    result['status'] = 'completed_all_candidates_retained_pending_per_block_visual_review'
    # No raw source text, exception text, or model values are present in this summary.
    write_json(output / 'receipt.json', result)
    write_json(public, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--sources', required=True)
    parser.add_argument('--external-output', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = execute(args.protocol, args.sources, args.external_output, args.output)
    print(json.dumps({k: result[k] for k in ['status', 'requested_blocks', 'rendered_blocks',
                                          'retained_failed_or_unrenderable_blocks']}))


if __name__ == '__main__':
    main()
