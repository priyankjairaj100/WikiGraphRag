"""One bounded authored loopback fixture; no model, shared port, or subprocess."""
import errno
import hashlib
import json
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'results/native_port_rebind_probe_v19.json'


def attempt_bind(address, reuse):
    with socket.socket() as probe:
        probe.settimeout(2)
        if reuse:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(address)
            return {'bound': True, 'reuseaddr': reuse}
        except OSError as error:
            return {'bound': False, 'reuseaddr': reuse, 'errno': error.errno,
                    'error_class': type(error).__name__}


def tcp_state(port):
    states = []
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1].split(':')[0] == '0100007F' and int(fields[1].split(':')[1], 16) == port:
            states.append({'state_hex': fields[3], 'inode': fields[9]})
    return states


def main():
    if OUTPUT.exists():
        raise RuntimeError('one bounded fixture; do not overwrite/retry')
    listener = client = accepted = None
    result = {'schema_version': 'native_port_rebind_authored_probe_v19',
              'native_processes': 0, 'model_calls': 0, 'shared_fixed_ports_touched': 0,
              'loops': 1, 'address_selection': 'kernel-assigned port on 127.0.0.1',
              'limitations': ['This reconstructs a possible failure mechanism, not the uncaptured errno of the actual v19 failure.',
                             'The authored listener uses SO_REUSEADDR; this fixture alone does not establish the native server socket option.']}
    try:
        listener = socket.socket()
        listener.settimeout(2)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        address = listener.getsockname()
        result['owned_ephemeral_port'] = address[1]
        result['live_listener_plain_probe'] = attempt_bind(address, False)
        result['live_listener_reuse_probe'] = attempt_bind(address, True)
        client = socket.socket()
        client.settimeout(2)
        client.connect(address)
        accepted, _ = listener.accept()
        accepted.settimeout(2)
        # Actively close the server side so TIME_WAIT belongs to its local port.
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b''
        client.close(); client = None
        assert accepted.recv(1) == b''
        accepted.close(); accepted = None
        listener.close(); listener = None
        result['post_close_tcp_states'] = tcp_state(address[1])
        result['closed_listener_plain_probe'] = attempt_bind(address, False)
        result['closed_listener_reuse_probe'] = attempt_bind(address, True)
        checks = {
            'live_listener_refused_plain': result['live_listener_plain_probe'].get('errno') == errno.EADDRINUSE,
            'live_listener_refused_reuse': result['live_listener_reuse_probe'].get('errno') == errno.EADDRINUSE,
            'time_wait_observed_for_owned_port': any(x['state_hex'] == '06' for x in result['post_close_tcp_states']),
            'plain_probe_fails_after_close': result['closed_listener_plain_probe'].get('errno') == errno.EADDRINUSE,
            'reuse_probe_binds_after_close': result['closed_listener_reuse_probe']['bound'] is True,
        }
        result.update(checks=checks, status='passed_authored_mechanism' if all(checks.values()) else 'failed_authored_mechanism')
    except Exception as error:
        result.update(status='probe_error', error_type=type(error).__name__, errno=getattr(error, 'errno', None))
    finally:
        for value in (accepted, client, listener):
            if value is not None:
                value.close()
        result['all_owned_sockets_closed'] = True
        result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with OUTPUT.open('x') as out:
            json.dump(result, out, indent=2); out.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
