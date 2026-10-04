"""Read-only v18 loader policy: total-boundary/max events are diagnostic.

This is a relaxation of v17, not an equivalent guard or a memory reservation.
No cgroup writes or process operations occur here. Sequential event snapshots
must be supplied by the caller, including before/after asset verification.
"""
from pathlib import Path
import time
import cgroup_guard_v16_2 as prior

COMPONENTS = prior.COMPONENTS
EVENTS = ('max', 'oom', 'oom_kill')
KERNEL_MAX_BYTES = prior.KERNEL_MAX_BYTES
PROXY_LIMIT_BYTES = prior.PROXY_LIMIT_BYTES


def read_snapshot(cgroup='/sys/fs/cgroup'):
    base = Path(cgroup)
    result = {'sample_monotonic_ns': time.monotonic_ns(), 'telemetry_ok': False}
    try:
        result['memory_current_bytes'] = prior._number((base / 'memory.current').read_text())
        result['memory_max_bytes'] = prior._number((base / 'memory.max').read_text())
        stat = prior._fields(base / 'memory.stat')
        result['components'] = {key: stat[key] for key in COMPONENTS}
        events = prior._fields(base / 'memory.events')
        result['events'] = {key: events[key] for key in EVENTS}
        result['pressure_proxy_bytes'] = sum(result['components'].values())
        result['total_boundary_observed'] = result['memory_current_bytes'] >= result['memory_max_bytes']
        result['telemetry_ok'] = True
    except (OSError, ValueError, KeyError) as error:
        result['telemetry_error_class'] = type(error).__name__
    return result


def valid(snapshot):
    try:
        if snapshot.get('telemetry_ok') is not True:
            return False
        numbers = [snapshot['memory_current_bytes'], snapshot['memory_max_bytes'],
                   snapshot['pressure_proxy_bytes']]
        numbers += [snapshot['events'][key] for key in EVENTS]
        numbers += [snapshot['components'][key] for key in COMPONENTS]
        return (all(type(value) is int and value >= 0 for value in numbers)
                and snapshot['pressure_proxy_bytes'] == sum(snapshot['components'].values())
                and set(snapshot['components']) == set(COMPONENTS))
    except (KeyError, TypeError, AttributeError):
        return False


def policy_reason(snapshot, baseline=None, reserve_bytes=0):
    if not valid(snapshot) or (baseline is not None and not valid(baseline)):
        return 'cgroup_telemetry_unavailable'
    if baseline is not None and any(snapshot['events'][key] < baseline['events'][key] for key in EVENTS):
        return 'cgroup_event_counter_reset'
    # prior enforces the exact kernel maximum, OOM deltas, and proxy/reserve.
    # Its event list excludes max: increasing max alone is intentionally allowed.
    return prior.policy_reason(snapshot, baseline, reserve_bytes)
