"""Read-only owned-group RSS with authoritative outer/inner PID binding.

The outer IDs are accounting metadata only. Signal the caller-created INNER
Popen.pid/PGID; this module never signals a process or changes any setting.
"""
import os
from pathlib import Path


def parse_status(text):
    values = {}
    for line in text.splitlines():
        if ':' in line:
            name, value = line.split(':', 1)
            if name in values:
                raise ValueError('duplicate_process_status_field')
            values[name] = value.strip()
    for name in ['NSpid', 'NSpgid']:
        fields = values[name].split()
        if not fields or any(not n.isdecimal() or int(n) <= 0 for n in fields):
            raise ValueError('invalid_namespace_pid_mapping')
        values[name] = [int(n) for n in fields]
    if len(values['NSpid']) != len(values['NSpgid']):
        raise ValueError('inconsistent_namespace_mapping_depth')
    state = values['State'].split()[0]
    if state not in {'R', 'S', 'D', 'Z', 'T', 't', 'X', 'x', 'K', 'W', 'P', 'I'}:
        raise ValueError('invalid_process_state')
    values['state_code'] = state
    if 'VmRSS' in values:
        fields = values['VmRSS'].split()
        if len(fields) != 2 or not fields[0].isdecimal() or fields[1] != 'kB':
            raise ValueError('invalid_process_rss')
        values['rss_bytes'] = int(fields[0]) * 1024
        if not values['rss_bytes'] and state not in {'Z', 'X', 'x'}:
            raise ValueError('live_process_rss_nonpositive')
    elif state in {'Z', 'X', 'x'}:
        values['rss_bytes'] = 0
    else:
        raise ValueError('live_process_rss_missing')
    return values


def parse_stat(text):
    prefix, rest = text[:text.index('(')].strip(), text[text.rfind(')') + 2:].split()
    if not prefix.isdecimal() or len(rest) < 20:
        raise ValueError('malformed_process_stat')
    values = [int(prefix), int(rest[1]), int(rest[2]), int(rest[3]), int(rest[19])]
    if any(value < 0 for value in values) or values[0] == 0:
        raise ValueError('invalid_process_stat_identity')
    return dict(zip(['outer_pid', 'outer_ppid', 'outer_pgid', 'outer_sid', 'starttime'], values))


def _error(record, error):
    record.update(telemetry_ok=False, error_type=type(error).__name__,
                  error_code=str(error) if isinstance(error, ValueError) else 'process_telemetry_unavailable')
    return record


def bind_group(inner_pgid, *, proc_root='/proc'):
    """Bind a direct Popen child created with start_new_session=True, once.

    Read the outer parent identity and its authoritative direct-child list, then
    verify the inner Popen PID, outer parent, namespace, fresh process group and
    fresh session. Preserve this anchor after the launcher exits.
    """
    anchor = {'telemetry_ok': False, 'inner_pgid': inner_pgid, 'proc_root': str(proc_root)}
    try:
        if type(inner_pgid) is not int or inner_pgid <= 0:
            raise ValueError('invalid_inner_process_group')
        base = Path(proc_root)
        parent = parse_status((base / 'self/status').read_text())
        if parent['NSpid'][-1] != os.getpid() or parent['NSpgid'][-1] != os.getpgrp():
            raise ValueError('caller_namespace_identity_mismatch')
        outer_parent = int(parent['Pid'])
        if outer_parent != parent['NSpid'][0]:
            raise ValueError('outer_parent_mapping_mismatch')
        namespace = os.readlink(base / 'self/ns/pid')
        try:
            children = (base / str(outer_parent) / 'task' / str(outer_parent) / 'children').read_text().split()
            anchor['direct_child_binding_method'] = 'outer_parent_task_children'
        except FileNotFoundError:
            # Some managed /proc mounts omit the children interface. Raw stat's
            # globally unique outer PPID gives the same direct-parent relation.
            # This fallback is not used for a permission-denied interface.
            children = []
            for path in base.iterdir():
                if not path.name.isdecimal():
                    continue
                try:
                    identity = parse_stat((path / 'stat').read_text())
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if identity['outer_ppid'] == outer_parent:
                    children.append(path.name)
            anchor['direct_child_binding_method'] = 'exact_outer_ppid_scan_children_interface_absent'
        if any(not p.isdecimal() or int(p) <= 0 for p in children):
            raise ValueError('invalid_direct_children_list')
        matches = []
        for outer in children:
            path = base / outer
            try:
                child = parse_status((path / 'status').read_text())
                stat = parse_stat((path / 'stat').read_text())
            except (FileNotFoundError, ProcessLookupError):
                continue
            if child['NSpid'][-1] != inner_pgid:
                continue
            sid = [int(x) for x in child['NSsid'].split()]
            if (len(child['NSpid']) != len(parent['NSpid']) or len(sid) != len(parent['NSpid'])
                    or child['NSpid'][0] != int(outer) or int(child['Pid']) != int(outer)
                    or int(child['PPid']) != outer_parent or stat['outer_ppid'] != outer_parent
                    or stat['outer_pid'] != int(outer) or stat['outer_pgid'] != int(outer)
                    or stat['outer_sid'] != int(outer) or child['NSpgid'][0] != int(outer)
                    or child['NSpgid'][-1] != inner_pgid or sid[0] != int(outer) or sid[-1] != inner_pgid
                    or os.readlink(path / 'ns/pid') != namespace):
                raise ValueError('owned_direct_child_identity_mismatch')
            matches.append(stat)
        if len(matches) != 1:
            raise ValueError('owned_direct_child_anchor_missing_or_ambiguous')
        anchor.update(matches[0], caller_outer_pid=outer_parent, namespace_identity=namespace,
                      namespace_depth=len(parent['NSpid']), telemetry_ok=True)
    except (OSError, ValueError, KeyError, IndexError) as error:
        return _error(anchor, error)
    return anchor


def read_group_rss(inner_pgid, *, anchor=None, proc_root='/proc'):
    """Read every member of the anchored unique OUTER process group.

    Missing ownership or live RSS fails closed. A no-members snapshot requires a
    fresh Popen.poll() before continuation; it is not RSS=0 for a live worker.
    Capture anchor=bind_group(Popen.pid) immediately after creation and retain it.
    """
    result = {'telemetry_ok': False, 'inner_pgid': inner_pgid, 'members': [], 'disappeared_entries': 0}
    try:
        if not anchor or not anchor.get('telemetry_ok') or anchor['inner_pgid'] != inner_pgid:
            raise ValueError('valid_owned_group_anchor_required')
        base = Path(proc_root)
        if str(proc_root) != anchor['proc_root'] or os.readlink(base / 'self/ns/pid') != anchor['namespace_identity']:
            raise ValueError('accounting_namespace_changed')
        own = parse_status((base / 'self/status').read_text())
        if own['NSpid'][0] != anchor['caller_outer_pid'] or own['NSpid'][-1] != os.getpid():
            raise ValueError('accounting_caller_changed')
        result['ownership_anchor'] = anchor
        for path in base.iterdir():
            if not path.name.isdecimal():
                continue
            try:
                stat = parse_stat((path / 'stat').read_text())
            except (FileNotFoundError, ProcessLookupError):
                result['disappeared_entries'] += 1
                continue
            # An outer PGID is unique in the proc mount's PID namespace. No
            # ambiguous inner-ID/namespace-symlink inference is used for others.
            if stat['outer_pgid'] != anchor['outer_pgid']:
                continue
            try:
                member = parse_status((path / 'status').read_text())
                namespace = os.readlink(path / 'ns/pid')
                checked = parse_stat((path / 'stat').read_text())
            except (FileNotFoundError, ProcessLookupError):
                result['disappeared_entries'] += 1
                continue
            if (checked != stat or namespace != anchor['namespace_identity']
                    or len(member['NSpid']) != anchor['namespace_depth']
                    or member['NSpid'][0] != stat['outer_pid']
                    or member['NSpgid'][0] != anchor['outer_pgid']
                    or member['NSpgid'][-1] != inner_pgid):
                raise ValueError('owned_member_identity_changed_or_mismatched')
            if stat['outer_pid'] == anchor['outer_pid'] and stat['starttime'] != anchor['starttime']:
                raise ValueError('owned_leader_pid_reused')
            result['members'].append({'outer_pid': stat['outer_pid'], 'inner_pid': member['NSpid'][-1],
                                      'inner_pgid': inner_pgid, 'rss_bytes': member['rss_bytes'],
                                      'state': member['state_code']})
        result['rss_bytes'] = sum(m['rss_bytes'] for m in result['members'])
        result['group_members_observed'] = bool(result['members'])
        result['telemetry_ok'] = True
    except (OSError, ValueError, KeyError, IndexError) as error:
        return _error(result, error)
    return result
