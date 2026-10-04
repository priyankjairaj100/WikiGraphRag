"""Read-only conservative cgroup-v2 pressure watchdog for small local workers.

The proxy intentionally double counts overlapping fields. It is not an exact
resident-memory estimate and does not preserve the former total-current cap.
This module never writes cgroup settings or signals any process.
"""
from pathlib import Path
import time

COMPONENTS = ('anon', 'file_mapped', 'shmem', 'kernel', 'sock', 'file_dirty',
              'file_writeback', 'unevictable', 'swapcached')
EVENTS = ('oom', 'oom_kill')
KERNEL_MAX_BYTES = 8 * 1024**3
PROXY_LIMIT_BYTES = 7 * 1024**3
WORKER_RESERVE_BYTES = 2 * 1024**3

def _number(text):
    value = int(text)
    if value < 0:
        raise ValueError('negative_telemetry')
    return value

def _fields(path):
    result = {}
    for line in path.read_text().splitlines():
        key, raw = line.split()
        if key in result:
            raise ValueError('duplicate_telemetry_field')
        result[key] = _number(raw)
    return result

def read_snapshot(cgroup='/sys/fs/cgroup'):
    """Return JSON-safe telemetry; unavailable/malformed inputs fail closed.

    Kernel files are read consecutively, not atomically. All required fields
    must exist and be nonnegative integers; memory.max must be a finite integer.
    """
    base = Path(cgroup)
    result = {'sample_monotonic_ns': time.monotonic_ns(), 'telemetry_ok': False}
    try:
        result['memory_current_bytes'] = _number((base/'memory.current').read_text())
        result['memory_max_bytes'] = _number((base/'memory.max').read_text())
        stat = _fields(base/'memory.stat')
        result['components'] = {key: stat[key] for key in COMPONENTS}
        events = _fields(base/'memory.events')
        result['events'] = {key: events[key] for key in EVENTS}
        result['pressure_proxy_bytes'] = sum(result['components'].values())
        result['telemetry_ok'] = True
    except (OSError, ValueError, KeyError) as error:
        result['telemetry_error_class'] = type(error).__name__
    return result

def policy_reason(snapshot, baseline=None, reserve_bytes=0):
    """None permits launch/continuation; otherwise return a stop reason.

    Before launch use WORKER_RESERVE_BYTES. During monitoring use zero reserve
    and the source's prelaunch baseline to detect any new OOM/OOM-kill event.
    """
    if not snapshot.get('telemetry_ok'):
        return 'cgroup_telemetry_unavailable'
    if snapshot['memory_max_bytes'] != KERNEL_MAX_BYTES:
        return 'unexpected_kernel_memory_max'
    if baseline is not None:
        if not baseline.get('telemetry_ok'):
            return 'cgroup_telemetry_unavailable'
        for key in EVENTS:
            if snapshot['events'][key] < baseline['events'][key]:
                return 'cgroup_event_counter_reset'
            if snapshot['events'][key] > baseline['events'][key]:
                return 'new_cgroup_oom_event'
    if reserve_bytes < 0:
        return 'invalid_worker_reserve'
    if snapshot['pressure_proxy_bytes'] + reserve_bytes >= PROXY_LIMIT_BYTES:
        return 'cgroup_pressure_proxy_limit'
    return None
