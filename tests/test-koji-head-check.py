#!/usr/bin/env python3
"""Offline tests for ci/koji-head-check.py against a local HEAD fixture server."""
import importlib.util
import json
import os
import socket
import urllib.error
import urllib.request
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ci/koji-head-check.py'
GUARD = 'KOJI_HEAD_CHECK_TESTING'
spec = importlib.util.spec_from_file_location('koji_head_check', SCRIPT)
khc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(khc)
REQUESTS = []
FLAKY_SEEN = set()
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format: str, *args: object) -> None:
        pass

    def reply(self, code, length=None, location=None):
        self.send_response(code)
        if length is not None:
            self.send_header('Content-Length', str(length))
        else:
            self.send_header('Content-Length', '0')
        if location:
            self.send_header('Location', location)
        self.end_headers()

    def do_GET(self):
        self.reply(405)

    def do_HEAD(self):
        with LOCK:
            REQUESTS.append((self.command, self.path))
        p = self.path
        if p == '/ok':
            self.reply(200, 5)
        elif p == '/nolen':
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'identity')
            self.send_header('Connection', 'close')
            self.end_headers()
        elif p == '/gone404':
            self.reply(404)
        elif p == '/gone410':
            self.reply(410)
        elif p == '/flaky':
            with LOCK:
                first = p not in FLAKY_SEEN
                FLAKY_SEEN.add(p)
            self.reply(503) if first else self.reply(200, 5)
        elif p == '/forbidden':
            self.reply(403)
        elif p == '/forbid-then-ok':
            with LOCK:
                first = p not in FLAKY_SEEN
                FLAKY_SEEN.add(p)
            self.reply(403) if first else self.reply(200, 5)
        elif p == '/rate429':
            self.reply(429)
        elif p == '/mixed':
            with LOCK:
                n = sum(1 for _, q in REQUESTS if q == p)
            self.reply((403, 401, 403)[(n - 1) % 3])
        elif p == '/badlen':
            self.send_response(200)
            self.send_header('Content-Length', 'abc')
            self.end_headers()
        elif p == '/always503':
            self.reply(503)
        elif p == '/wrongsize':
            self.reply(200, 99)
        elif p == '/redir':
            self.reply(302, location='/ok')
        elif p == '/redir-loop':
            self.reply(302, location='/redir-loop')
        elif p == '/redir-bad':
            self.reply(302, location='ftp://example.invalid/x')
        elif p == '/slow':
            time.sleep(1.5)
            self.reply(200, 5)
        else:
            self.reply(404)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}/'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        with LOCK:
            REQUESTS.clear()
            FLAKY_SEEN.clear()

    def pin(self, rpms, base='https://kojipkgs.fedoraproject.org/packages/', raw=None):
        path = Path(self.tmp.name) / f'pins-{time.monotonic_ns()}.json'
        path.write_text(raw if raw is not None else json.dumps({'kojiBase': base, 'toolchainRpms': rpms}))
        return path

    def run_tool(self, *args, guard=True):
        env = {k: v for k, v in os.environ.items() if k != GUARD and k != 'GITHUB_STEP_SUMMARY'}
        if guard:
            env[GUARD] = '1'
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)

    def check(self, paths_sizes, *extra):
        rpms = [{'name': f'p{i}', 'kojiPath': p, 'size': s} for i, (p, s) in enumerate(paths_sizes)]
        return self.run_tool('--pins', str(self.pin(rpms)), '--base-url-override', self.base,
                             '--timeout', '0.4', '--backoff', '0', *extra)


class StatusTests(Fixture):
    def test_all_ok_exit0(self):
        r = self.check([('ok', 5), ('nolen', 5), ('redir', 5), ('ok', None)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('OK=4', r.stdout)
        self.assertTrue(all(m == 'HEAD' for m, _ in REQUESTS))

    def test_404_gone_exit1(self):
        r = self.check([('gone404', 5)])
        self.assertEqual(r.returncode, 1)
        self.assertIn('GONE p0 gone404', r.stdout)
        self.assertEqual(len(REQUESTS), 1)  # definitive: no retries

    def test_410_gone_exit1(self):
        r = self.check([('gone410', 5)])
        self.assertEqual(r.returncode, 1)
        self.assertIn('GONE', r.stdout)

    def test_size_mismatch_exit1(self):
        r = self.check([('wrongsize', 5)])
        self.assertEqual(r.returncode, 1)
        self.assertIn('SIZE_MISMATCH p0 wrongsize', r.stdout)

    def test_503_then_200_retry_succeeds(self):
        r = self.check([('flaky', 5)])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(REQUESTS), 2)

    def test_always_503_transient_exit3_after_three_attempts(self):
        r = self.check([('always503', 5)])
        self.assertEqual(r.returncode, 3)
        self.assertIn('TRANSIENT p0 always503', r.stdout)
        self.assertEqual(len(REQUESTS), 3)

    def test_stable_403_forbidden_exit1(self):
        r = self.check([('forbidden', 5)])
        self.assertEqual(r.returncode, 1)
        self.assertIn('FORBIDDEN p0 forbidden (HTTP 403)', r.stdout)
        self.assertEqual(len(REQUESTS), 3)

    def test_403_then_200_ok(self):
        r = self.check([('forbid-then-ok', 5)])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(REQUESTS), 2)

    def test_429_and_mixed_forbidden_stay_transient(self):
        r = self.check([('rate429', 5)])
        self.assertEqual(r.returncode, 3)
        self.assertIn('TRANSIENT p0 rate429 (HTTP 429)', r.stdout)
        r = self.check([('mixed', 5)])
        self.assertEqual(r.returncode, 3, r.stdout)

    def test_non_numeric_content_length_transient(self):
        r = self.check([('badlen', 5)])
        self.assertEqual(r.returncode, 3)
        self.assertIn('TRANSIENT p0 badlen (bad Content-Length)', r.stdout)

    def test_transient_cause_printed(self):
        r = self.check([('always503', 5)])
        self.assertIn('TRANSIENT p0 always503 (HTTP 503)', r.stdout)
        rpms = [{'name': 'x', 'kojiPath': 'ok', 'size': 5}]
        r = self.run_tool('--pins', str(self.pin(rpms)), '--base-url-override', 'http://127.0.0.1:1/',
                          '--timeout', '0.4', '--backoff', '0')
        self.assertRegex(r.stdout, r'TRANSIENT x ok \((URLError|ConnectionRefusedError)\)')

    def test_garbage_status_line_transient_not_traceback(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(8)
        self.addCleanup(srv.close)

        def serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                conn.recv(4096)
                conn.sendall(b'NOT HTTP AT ALL\r\n\r\n')
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        rpms = [{'name': 'a', 'kojiPath': 'x', 'size': 5}, {'name': 'b', 'kojiPath': 'y', 'size': 5}]
        r = self.run_tool('--pins', str(self.pin(rpms)), '--base-url-override',
                          f'http://127.0.0.1:{srv.getsockname()[1]}/', '--timeout', '1', '--backoff', '0')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('TRANSIENT a x (BadStatusLine)', r.stdout)
        self.assertIn('TRANSIENT b y', r.stdout)  # later entries are still checked

    def test_deadline_zero_makes_no_requests(self):
        r = self.check([('ok', 5), ('ok', 5)], '--deadline', '0')
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.count('(deadline)'), 2)
        self.assertEqual(REQUESTS, [])

    def test_deadline_stops_remaining_entries(self):
        r = self.check([('slow', 5)] * 6, '--deadline', '1')
        self.assertEqual(r.returncode, 3)
        self.assertIn('(deadline)', r.stdout)
        self.assertLess(len(REQUESTS), 18)

    def test_production_redirect_to_http_refused(self):
        handler = khc.StrictRedirect(frozenset({'https'}))
        req = urllib.request.Request('https://k.example/a', method='HEAD')
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'http://k.example/a')
        out = handler.redirect_request(req, None, 302, 'Found', {}, 'https://k.example/b')
        self.assertEqual(out.get_method(), 'HEAD')
        self.assertEqual(handler.max_redirections, 3)

    def test_slow_response_transient(self):
        r = self.check([('slow', 5)])
        self.assertEqual(r.returncode, 3)
        self.assertIn('TRANSIENT', r.stdout)

    def test_connection_refused_transient(self):
        rpms = [{'name': 'x', 'kojiPath': 'ok', 'size': 5}]
        r = self.run_tool('--pins', str(self.pin(rpms)), '--base-url-override', 'http://127.0.0.1:1/',
                          '--timeout', '0.4', '--backoff', '0')
        self.assertEqual(r.returncode, 3)

    def test_redirect_loop_and_bad_scheme_transient(self):
        self.assertEqual(self.check([('redir-loop', 5)]).returncode, 3)
        self.assertEqual(self.check([('redir-bad', 5)]).returncode, 3)

    def test_gone_beats_transient(self):
        r = self.check([('always503', 5), ('gone404', 5)])
        self.assertEqual(r.returncode, 1)

    def test_output_path_only_and_capped(self):
        r = self.check([('ok', 5)] * 16)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 17)
        self.assertNotIn('http', r.stdout)
        self.assertNotIn('127.0.0.1', r.stdout)

    def test_step_summary_written(self):
        out = Path(self.tmp.name) / 'summary.md'
        env = {**os.environ, GUARD: '1', 'GITHUB_STEP_SUMMARY': str(out)}
        rpms = [{'name': 'a', 'kojiPath': 'gone404', 'size': 5}]
        subprocess.run([sys.executable, str(SCRIPT), '--pins', str(self.pin(rpms)), '--base-url-override',
                        self.base, '--backoff', '0'], env=env, capture_output=True, timeout=60)
        text = out.read_text()
        self.assertIn('| GONE | `a` | `gone404` |', text)


class PinErrorTests(Fixture):
    def refuses(self, pin, *extra):
        r = self.run_tool('--pins', str(pin), '--base-url-override', self.base, *extra)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(REQUESTS, [])
        return r

    good = {'name': 'a', 'kojiPath': 'ok', 'size': 5}

    def test_bad_koji_base(self):
        for base in ('http://kojipkgs.fedoraproject.org/packages/', 'https://k.example/packages',
                     'https://k.example/pack ages/', 'ftp://x/', 'https://k.example/p/\n'):
            self.refuses(self.pin([self.good], base=base))

    def test_unsafe_paths(self):
        for path in ('a/../b', '/abs/path', 'bad path', 'a?b=c', '', 'ok\n'):
            self.refuses(self.pin([{**self.good, 'kojiPath': path}]))

    def test_bad_size_and_shape(self):
        self.refuses(self.pin([{**self.good, 'size': '5'}]))
        self.refuses(self.pin([{**self.good, 'size': True}]))
        self.refuses(self.pin(['nope']))
        self.refuses(self.pin([]))

    def test_missing_file_and_invalid_json(self):
        self.refuses(Path(self.tmp.name) / 'does-not-exist.json')
        self.refuses(self.pin(None, raw='{not json'))
        self.refuses(self.pin(None, raw='[]'))

    def test_one_bad_file_refuses_whole_run(self):
        good, bad = self.pin([self.good]), self.pin([{**self.good, 'kojiPath': '../x'}])
        r = self.run_tool('--pins', str(good), '--pins', str(bad), '--base-url-override', self.base)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(REQUESTS, [])

    def test_bad_override(self):
        r = self.run_tool('--pins', str(self.pin([self.good])), '--base-url-override', 'ftp://x/')
        self.assertEqual(r.returncode, 2)


class GuardAndListTests(Fixture):
    def test_test_only_flags_refused_without_guard(self):
        pin = self.pin([{'name': 'a', 'kojiPath': 'ok', 'size': 5}])
        for args in (['--pins', str(pin)], ['--base-url-override', self.base]):
            r = self.run_tool(*args, guard=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn('test-only', r.stderr)
        self.assertEqual(REQUESTS, [])

    def test_real_pins_list_all_no_network(self):
        expected = len(khc.collect(khc.DEFAULT_PINS, None))
        self.assertGreater(expected, 0)
        r = self.run_tool('--list', guard=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(sum(l.startswith('WOULD-CHECK ') for l in lines), expected)
        self.assertIn(f'{expected} entries would be checked', lines[-1])
        self.assertNotIn('sha256', r.stdout)
        self.assertNotIn('http', r.stdout)

    def test_list_makes_no_request(self):
        pin = self.pin([{'name': 'a', 'kojiPath': 'ok', 'size': 5}])
        r = self.run_tool('--list', '--pins', str(pin), '--base-url-override', self.base)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(REQUESTS, [])


if __name__ == '__main__':
    unittest.main()
