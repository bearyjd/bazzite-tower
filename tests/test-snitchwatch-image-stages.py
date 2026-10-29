#!/usr/bin/env python3
"""H1: the snitchwatch-system build is split into stages so that the bridge's
third-party build scripts cannot reach the scanner, the daemon or the assembly.

Static Containerfile/workflow structure plus behavioural tests of the stage
guards (snitchwatch-system-handoff.py) and of stage.py's unprivileged bridge
execution. Runs offline with python3 only (no rpm, cargo, dnf or containers).
What only a real CI build can prove is listed in docs/CODEMAPS/image-build.md.
"""
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
CONTAINERFILE = ROOT / 'Containerfile'
PINS = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
BUILD_STAGES = ('snitchwatch-prep', 'snitchwatch-scanner', 'snitchwatch-bridge', 'snitchwatch-daemon', 'snitchwatch-system')
OFFLINE_STAGES = ('snitchwatch-scanner', 'snitchwatch-system')
# Scripts and fragments that execute third-party build code or the bridge binary.
BRIDGE_ONLY = ('snitchwatch-system-bridge-build.sh', 'build-bridge.sh', 'repro-check.sh', '--run-flags', '--check-ldd')
HANDOFF = FACTORY / 'snitchwatch-system-handoff.py'


def instructions(text):
    """[(keyword, rest)] with continuation lines joined and comments dropped."""
    result, pending = [], ''
    for line in text.splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith('#')):
            continue
        pending += line.rstrip()
        if pending.endswith('\\'):
            pending = pending[:-1] + ' '
            continue
        keyword, _, rest = pending.strip().partition(' ')
        result.append((keyword.upper(), rest.strip()))
        pending = ''
    return result


def stages(text=None):
    """{name: {'from': base, 'body': [(keyword, rest)], 'index': n}} in file order."""
    found, current = {}, None
    for keyword, rest in instructions(CONTAINERFILE.read_text() if text is None else text):
        if keyword == 'FROM':
            parts = rest.split()
            name = parts[2] if len(parts) == 3 and parts[1].upper() == 'AS' else '<final-%d>' % len(found)
            current = found[name] = {'from': parts[0], 'body': [], 'index': len(found)}
        elif current is not None:
            current['body'].append((keyword, rest))
    return found


def from_chain(all_stages, name):
    chain = []
    while name in all_stages:
        chain.append(name)
        name = all_stages[name]['from']
    return chain


def copy_sources(stage):
    return [re.match(r'--from=(\S+)', rest).group(1) for keyword, rest in stage['body']
            if keyword == 'COPY' and rest.startswith('--from=')]


def runs(stage):
    return [rest for keyword, rest in stage['body'] if keyword == 'RUN']


def script_text(run):
    """The RUN text plus the content of every factory script it invokes."""
    text = run
    for name in re.findall(r'/(?:factory|tools)/(snitchwatch-system-[\w.-]+)', run):
        text += '\n' + (FACTORY / name).read_text()
    return text


class ContainerfileStages(unittest.TestCase):
    def setUp(self):
        self.stages = stages()

    def test_stage_names_order_and_from_chain(self):
        names = [name for name in self.stages if not name.startswith('<final')]
        self.assertEqual(names, ['ctx', 'snitchwatch-legacy', *BUILD_STAGES, 'snitchwatch-selected'])
        builder = PINS['builderImage']
        self.assertEqual(self.stages['snitchwatch-prep']['from'], builder)
        daemon_pins = json.loads((FACTORY / 'snitchwatch-system-daemon-pins.json').read_text())
        self.assertEqual(daemon_pins['builderImage'], builder)
        for name in BUILD_STAGES[1:]:
            self.assertEqual(self.stages[name]['from'], 'snitchwatch-prep', name)
        self.assertEqual(self.stages['snitchwatch-legacy']['from'], 'scratch')
        self.assertEqual(self.stages['ctx']['from'], 'scratch')
        self.assertEqual(self.stages['snitchwatch-selected']['from'], 'snitchwatch-${SNITCHWATCH_BRIDGE}')
        final = [stage for name, stage in self.stages.items() if name.startswith('<final')]
        self.assertEqual([stage['from'] for stage in final], ['${BASE_IMAGE}'])
        self.assertIn(('COPY', '--from=snitchwatch-selected /out/ /'), final[0]['body'])

    def test_no_bridge_build_in_the_ancestry_of_scanner_daemon_or_assembly(self):
        for name in ('snitchwatch-scanner', 'snitchwatch-daemon', 'snitchwatch-system'):
            chain = from_chain(self.stages, name)
            self.assertNotIn('snitchwatch-bridge', chain, name)
            for ancestor in chain:
                for run in runs(self.stages[ancestor]):
                    text = script_text(run)
                    for needle in BRIDGE_ONLY:
                        self.assertNotIn(needle, text, '%s (ancestor of %s) reaches %s' % (ancestor, name, needle))
        # The bridge stage is the base of nothing and is read only by the assembly COPY.
        self.assertEqual([n for n, s in self.stages.items() if s['from'] == 'snitchwatch-bridge'], [])
        readers = [n for n, s in self.stages.items() if 'snitchwatch-bridge' in copy_sources(s)]
        self.assertEqual(readers, ['snitchwatch-system'])
        self.assertTrue(any('snitchwatch-system-bridge-build.sh' in run for run in runs(self.stages['snitchwatch-bridge'])))
        self.assertTrue(any('snitchwatch-system-daemon-build.sh' in run for run in runs(self.stages['snitchwatch-daemon'])))

    def test_daemon_build_runs_only_in_the_daemon_stage(self):
        for name, stage in self.stages.items():
            for run in runs(stage):
                if 'daemon-build.sh' in script_text(run):
                    self.assertEqual(name, 'snitchwatch-daemon')

    def test_network_none_is_on_every_offline_run_and_confined_to_those_stages(self):
        for name, stage in self.stages.items():
            for run in runs(stage):
                if name in OFFLINE_STAGES:
                    self.assertTrue(run.startswith('--network=none '), name)
                else:
                    # Confinement: a builder that rejects the flag breaks only these legs' stages.
                    self.assertNotIn('--network', run, name)
            if name in OFFLINE_STAGES:
                self.assertEqual(len(runs(stage)), 1, name)
        self.assertEqual(CONTAINERFILE.read_text().count('--network='), 2)

    def test_offline_stage_scripts_probe_the_network_first(self):
        for script in ('snitchwatch-system-scanner-stage.sh', 'snitchwatch-system-assemble.sh'):
            text = (FACTORY / script).read_text()
            probe = text.index('probe-network offline')
            for later in ('rpm -qa', 'python3 "$factory/snitchwatch-system-scanner-build.py"', '"$guard" check'):
                if later in text:
                    self.assertLess(probe, text.index(later), script)
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" probe-network online', prep)

    def test_copy_from_handoffs_are_exactly_the_allowlisted_paths(self):
        allowed = {
            'snitchwatch-prep': ['--from=ctx /firewall/snitchwatch-system* /factory/'],
            'snitchwatch-system': ['--from=ctx /firewall/snitchwatch-system* /tools/',
                                   '--from=snitchwatch-scanner /handoff/scanner/ /handoff/scanner/',
                                   '--from=snitchwatch-bridge /handoff/bridge/ /handoff/bridge/',
                                   '--from=snitchwatch-daemon /handoff/daemon/ /handoff/daemon/'],
        }
        for name in BUILD_STAGES:
            copies = [rest for keyword, rest in self.stages[name]['body'] if keyword == 'COPY' and rest.startswith('--from=')]
            self.assertEqual(copies, allowed.get(name, []), name)
            for keyword, _ in self.stages[name]['body']:
                self.assertIn(keyword, ('ARG', 'COPY', 'RUN'), name)
        # Only the (scratch) build context and the final overlay are read by the final image.
        final = next(s for n, s in self.stages.items() if n.startswith('<final'))
        self.assertEqual(copy_sources(final), ['snitchwatch-selected'])

    def test_assembly_runs_tooling_only_from_ctx_and_the_rechecked_prep_checkout(self):
        run = runs(self.stages['snitchwatch-system'])[0]
        self.assertRegex(run, r' bash /tools/snitchwatch-system-assemble\.sh /work/native /handoff /out$')
        text = (FACTORY / 'snitchwatch-system-assemble.sh').read_text()
        body = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        executed = re.findall(r'(?:python3(?: -B)?|bash) "([^"]+)"', body)
        self.assertTrue(executed)
        for path in executed:
            self.assertTrue(path.startswith(('$factory/', '$guard', '$daemon_verifier', '$work/source/')), path)
        for needle in ('cargo', 'rustc', 'build-bridge', 'repro-check', '--run-flags', '--check-ldd', 'go build', 'dnf', 'curl', 'git '):
            self.assertNotIn(needle, body)
        # The handoffs are checked before anything reads them, and merged last.
        order = ['probe-network offline', 'cmp - "$work/rpm-inventory.txt"', "p['sourceFiles'].items()",
                 '"$guard" check scanner', '"$guard" check bridge', '"$guard" check daemon',
                 'bridge_artifact.py" verify', 'snitchwatch-system-stage.py"', '"$guard" merge',
                 '"$daemon_verifier" --stage-only --root "$out"', 'snitchwatch-system-verify.py" --root "$out" --image-tree']
        positions = [body.index(needle) for needle in order]
        self.assertEqual(positions, sorted(positions))

    def test_every_consuming_stage_recomputes_the_shared_rpm_inventory(self):
        for script in ('snitchwatch-system-scanner-stage.sh', 'snitchwatch-system-bridge-build.sh', 'snitchwatch-system-assemble.sh'):
            text = (FACTORY / script).read_text()
            self.assertIn("rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\\n' | LC_ALL=C sort | cmp - \"$work/rpm-inventory.txt\"", text, script)
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertTrue(prep.rstrip().endswith('> "$work/rpm-inventory.txt"'))

    def test_prep_fetch_matches_the_bridge_build_fetch_and_runs_no_build(self):
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertIn('CARGO_HOME="$work/cargo" cargo fetch --locked --target x86_64-unknown-linux-gnu '
                      '--manifest-path "$work/source/Cargo.toml"', prep)
        for needle in ('cargo build', 'cargo test', 'cargo run', 'cargo install'):
            self.assertNotIn(needle, prep)

    def test_scanner_and_bridge_handoffs_are_exported_through_the_allowlist(self):
        scanner = (FACTORY / 'snitchwatch-system-scanner-stage.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" export scanner "$work/scanner" "$2"', scanner)
        bridge = (FACTORY / 'snitchwatch-system-bridge-build.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" export bridge --pins "$pins" "$OUT_DIR" "$2"', bridge)
        # All bridge execution (ldd, --version, --help) happens in the bridge stage.
        self.assertIn('--check-ldd --run-flags', bridge)

    def test_old_single_run_factory_is_gone(self):
        self.assertFalse((FACTORY / 'snitchwatch-system-build.sh').exists())
        self.assertNotIn('snitchwatch-system-build.sh', CONTAINERFILE.read_text())


class WorkflowCacheSettings(unittest.TestCase):
    """Every check runs on every build: no CI system build may reuse cached layers."""

    def test_no_layer_cache_or_remote_cache_in_image_builds(self):
        checked = 0
        for workflow in sorted((ROOT / '.github/workflows').glob('*.yml')):
            text = workflow.read_text()
            for match in re.finditer(r'uses: redhat-actions/buildah-build@\S+.*?(?=\n\s*- name:|\n\S|\Z)', text, re.S):
                block = match.group(0)
                checked += 1
                self.assertIsNone(re.search(r'^\s*layers:\s*["\']?true', block, re.M), workflow.name)
                self.assertIsNone(re.search(r'--layers|--cache-from|--cache-to', block), workflow.name)
            for command in re.findall(r'podman build[^\n]*(?:\\\n[^\n]*)*', text):
                self.assertIsNone(re.search(r'--cache-from|--cache-to|--layers=true', command), workflow.name)
        # build.yml (verify + release), base-refresh.yml and r13-candidate.yml.
        self.assertGreaterEqual(checked, 4)

    def test_new_stage_tests_run_in_both_source_safety_blocks(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-snitchwatch-image-stages.py'), 2)


class Handoff(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='sw-handoff.')
        self.addCleanup(work.cleanup)
        self.addCleanup(os.umask, os.umask(0o022))
        self.root = Path(work.name)
        self.h = runpy.run_path(str(HANDOFF))
        self.owner = (os.getuid(), os.getgid())

    def write(self, path, data=b'x', mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def scanner(self, root):
        for name, mode in self.h['allowlist']('scanner').items():
            self.write(root / name, name.encode(), mode)
        return root

    def check(self, kind, root, **extra):
        return self.h['check'](kind, root, PINS, owner=self.owner, **extra)

    def refused(self, kind, root, message, **extra):
        with self.assertRaises(self.h['Refusal']) as caught:
            self.check(kind, root, **extra)
        self.assertIn(message, str(caught.exception))

    def test_scanner_allowlist_matches_what_stage_py_and_the_verifier_consume(self):
        allowed = self.h['allowlist']('scanner')
        stage = (FACTORY / 'snitchwatch-system-stage.py').read_text()
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        evidence = set(verifier['SCANNER_EVIDENCE']) | {'LICENSE', 'THIRD-PARTY-LICENSES.md'}
        binaries = {Path(name).name for name in verifier['SCANNER_BINARIES']}
        self.assertEqual(set(allowed), evidence | binaries | {'provenance.json'})
        self.assertEqual({name for name, mode in allowed.items() if mode == 0o755}, binaries)
        self.assertIn("scanner / 'provenance.json'", stage)
        # Flat: no source export, cargo home or target directory crosses the boundary.
        self.assertFalse(any('/' in name or name.startswith(('source.', 'cargo', 'target')) for name in allowed))

    def test_bridge_allowlist_is_tarball_sidecar_and_logs(self):
        tarball = 'snitchwatch-bridge-%s-x86_64.tar.gz' % PINS['bridgeVersion']
        self.assertEqual(set(self.h['allowlist']('bridge', PINS)), {tarball, tarball + '.sha256', 'build.log', 'repro.log'})

    def test_daemon_allowlist_is_the_trusted_verifier_inventory(self):
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-daemon-verify.py'))
        allowed = self.h['allowlist']('daemon', daemon_verifier=FACTORY / 'snitchwatch-system-daemon-verify.py')
        self.assertEqual(set(allowed), {name.lstrip('/') for name in set(verifier['REQUIRED']) | {verifier['MANIFEST']}})
        self.assertEqual(allowed['usr/share/snitchwatch/daemon/opensnitchd'], 0o755)
        self.assertEqual(allowed['usr/libexec/snitchwatch/verify-system-daemon.py'], 0o755)
        with self.assertRaises(self.h['Refusal']):
            self.h['allowlist']('daemon')

    def test_valid_handoff_passes_and_export_copies_only_the_allowlist(self):
        source = self.scanner(self.root / 'scanner-out')
        for extra in ('source/packaging/release/bridge_artifact.py', 'cargo-home/registry/x.crate', 'target-a/release/x'):
            self.write(source / extra)
        dest = self.root / 'handoff/scanner'
        self.h['export']('scanner', source, dest)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(self.h['allowlist']('scanner')))
        self.assertEqual(len(self.check('scanner', dest)), 12)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o755)
        with self.assertRaises(self.h['Refusal']):
            self.h['export']('scanner', source, dest)

    def test_export_refuses_a_symlinked_source_file(self):
        source = self.scanner(self.root / 'out')
        (source / 'build.log').unlink()
        (source / 'build.log').symlink_to('/etc/hostname')
        with self.assertRaises(self.h['Refusal']):
            self.h['export']('scanner', source, self.root / 'dest')

    def test_check_refuses_every_unexpected_shape(self):
        cases = {
            'extra file': (lambda r: self.write(r / 'evil.so'), 'unexpected file'),
            'source tree': (lambda r: self.write(r / 'source/packaging/system/stage.sh'), 'unexpected directory'),
            'missing file': (lambda r: (r / 'test.log').unlink(), 'missing: test.log'),
            'symlink': (lambda r: ((r / 'LICENSE').unlink(), (r / 'LICENSE').symlink_to('/etc/passwd')), 'symlink or special'),
            'dir symlink': (lambda r: (r / 'sub').symlink_to('/'), 'symlink or special'),
            'fifo': (lambda r: os.mkfifo(r / 'pipe'), 'symlink or special'),
            'setuid': (lambda r: (r / 'snitchwatch-scanner-broker').chmod(0o4755), 'setuid'),
            'setgid dir': (lambda r: r.chmod(0o2755), 'setuid'),
            'wrong mode': (lambda r: (r / 'provenance.json').chmod(0o755), 'has mode 755, want 644'),
            'writable dir': (lambda r: r.chmod(0o777), 'group/other writable'),
            'hard link': (lambda r: os.link(r / 'build.log', self.root / ('link-' + r.name)), 'hard-linked'),
        }
        for label, (mutate, message) in cases.items():
            with self.subTest(label):
                root = self.scanner(self.root / label.replace(' ', '-'))
                mutate(root)
                self.refused('scanner', root, message)

    def test_check_refuses_wrong_owner_capabilities_and_acls(self):
        root = self.scanner(self.root / 'owner')
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['check']('scanner', root, owner=(self.owner[0], self.owner[1] + 1))
        self.assertIn('is owned by', str(caught.exception))
        for attribute in ('security.capability', 'system.posix_acl_access'):
            with self.subTest(attribute), mock.patch('os.listxattr', return_value=[attribute]):
                self.refused('scanner', root, 'refused extended attribute ' + attribute)
        with mock.patch('os.listxattr', return_value=['security.selinux']):
            self.assertEqual(len(self.check('scanner', root)), 12)
        with self.assertRaises(OSError):
            self.check('scanner', self.root / 'absent')

    def test_merge_adds_the_overlay_and_refuses_any_overlap(self):
        overlay = self.root / 'daemon'
        self.write(overlay / 'usr/share/snitchwatch/daemon/opensnitchd', b'elf', 0o755)
        self.write(overlay / 'usr/share/licenses/snitchwatch-opensnitchd/LICENSE', b'gpl')
        out = self.root / 'out'
        self.write(out / 'usr/share/snitchwatch/system-bridge-manifest.json', b'{}')
        added = self.h['merge'](overlay, out)
        self.assertEqual(sorted(added), ['usr/share/licenses/snitchwatch-opensnitchd/LICENSE', 'usr/share/snitchwatch/daemon/opensnitchd'])
        self.assertEqual(stat.S_IMODE((out / 'usr/share/snitchwatch/daemon/opensnitchd').stat().st_mode), 0o755)
        self.assertEqual((out / 'usr/share/snitchwatch/system-bridge-manifest.json').read_bytes(), b'{}')
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['merge'](overlay, out)
        self.assertIn('merge overlap', str(caught.exception))
        # A file where the overlay needs a directory, and a symlinked directory, are overlaps too.
        for label, prepare in (('file-for-dir', lambda o: self.write(o / 'usr/share/snitchwatch')),
                               ('symlink-dir', lambda o: (o / 'usr/share').mkdir(parents=True) or (o / 'usr/share/snitchwatch').symlink_to(self.root))):
            with self.subTest(label):
                target = self.root / label
                prepare(target)
                with self.assertRaises(self.h['Refusal']):
                    self.h['merge'](overlay, target)

    def test_cli_reports_a_distinct_refusal_line(self):
        root = self.scanner(self.root / 'cli')
        self.write(root / 'evil')
        result = subprocess.run(['python3', str(HANDOFF), 'check', 'scanner', str(root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r'^handoff: check: ')


class NetworkProbe(unittest.TestCase):
    def setUp(self):
        self.h = runpy.run_path(str(HANDOFF))
        work = tempfile.TemporaryDirectory(prefix='sw-probe.')
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)

    def dev(self, *names):
        path = self.root / 'dev'
        path.write_text('Inter-|   Receive\n face |bytes\n' + ''.join('%6s: 0 0 0\n' % name for name in names))
        return path

    @staticmethod
    def refuse(*_args, **_kwargs):
        raise OSError(101, 'Network is unreachable')

    @staticmethod
    def accept(*_args, **_kwargs):
        return mock.Mock()

    def resolve_ok(self, host, port, **_kwargs):
        return [(None, None, None, None, ('192.0.2.1', port))]

    def test_offline_passes_only_with_lo_and_every_attempt_failing(self):
        result = self.h['probe_network']('offline', self.dev('lo'), self.refuse, self.refuse)
        self.assertEqual(result['interfaces'], ['lo'])
        self.assertTrue(all(value != 'connected' for value in result['results'].values()))

    def test_offline_refuses_extra_interface(self):
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo', 'eth0'), self.refuse, self.refuse)
        self.assertIn('RUN --network=none is not in effect', str(caught.exception))

    def test_offline_refuses_any_successful_connection(self):
        # e.g. a builder that leaves a routable lo-only namespace or ignores the flag
        calls = iter([self.refuse, self.accept])
        connect = lambda *a, **k: next(calls)(*a, **k)
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo'), connect, self.refuse)
        self.assertIn('outbound connection succeeded', str(caught.exception))
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo'), self.refuse, self.resolve_ok)
        self.assertIn('dns registry.fedoraproject.org', str(caught.exception))

    def test_online_positive_control(self):
        self.h['probe_network']('online', self.dev('lo', 'eth0'), self.accept, self.resolve_ok)
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('online', self.dev('lo', 'eth0'), self.refuse, self.refuse)
        self.assertIn('positive control failed', str(caught.exception))

    def test_probe_targets_are_a_literal_address_and_a_name(self):
        self.assertTrue(all(re.fullmatch(r'[0-9.]+|[0-9a-f:]+', host) for host, _ in self.h['PROBE_ADDRESSES']))
        self.assertFalse(re.fullmatch(r'[0-9.:]+', self.h['PROBE_NAME'][0]))

    def test_interfaces_reads_this_netns(self):
        self.assertEqual(self.h['interfaces'](self.dev('lo', 'eth0')), ['eth0', 'lo'])


class UnprivilegedBridgeExecution(unittest.TestCase):
    """stage.py never runs the bridge binary (built by ~660 crates) as root."""

    def setUp(self):
        self.g = runpy.run_path(str(FACTORY / 'snitchwatch-system-stage.py'))
        self.real = self.g['main'].__globals__  # the functions' live globals
        work = tempfile.TemporaryDirectory(prefix='sw-unpriv.')
        self.addCleanup(work.cleanup)
        self.addCleanup(os.umask, os.umask(0o022))
        self.root = Path(work.name)

    def test_root_drops_to_65534_with_an_empty_environment(self):
        with mock.patch('os.geteuid', return_value=0):
            options = self.g['unprivileged_options']()
        self.assertEqual((options['user'], options['group'], options['extra_groups']), (65534, 65534, []))
        self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        self.assertEqual((options['cwd'], options['umask']), ('/', 0o022))
        with mock.patch('os.geteuid', return_value=1000):
            self.assertNotIn('user', self.g['unprivileged_options']())

    def test_both_bridge_executions_go_through_run_unprivileged(self):
        text = (FACTORY / 'snitchwatch-system-stage.py').read_text()
        self.assertIn("run_unprivileged([str(binary), '--version']", text)
        self.assertIn("run_unprivileged(['bash', str(source / 'packaging/system/stage.sh')", text)
        self.assertNotIn("check_output([str(binary)", text)
        self.assertEqual(text.count("'packaging/system/stage.sh'"), 1)

    def test_root_run_kills_leftover_processes_and_maps_permission_errors(self):
        calls = []
        fake = mock.Mock(side_effect=lambda *a, **k: calls.append(k) or mock.Mock(stdout='x'))
        with mock.patch.dict(self.real, {'kill_leftover_children': lambda: calls.append('killed'),
                                         'become_subreaper': lambda: calls.append('subreaper')}), \
                mock.patch('os.geteuid', return_value=0), mock.patch('subprocess.run', fake):
            self.g['run_unprivileged'](['true'])
        self.assertEqual(calls[0], 'subreaper')
        self.assertEqual(calls[1]['user'], 65534)
        self.assertEqual(calls[-1], 'killed')
        with mock.patch.dict(self.real, {'kill_leftover_children': lambda: None, 'become_subreaper': lambda: None}), \
                mock.patch('os.geteuid', return_value=0), mock.patch('subprocess.run', side_effect=PermissionError('setgroups')):
            with self.assertRaisesRegex(ValueError, 'cannot drop to uid/gid 65534'):
                self.g['run_unprivileged'](['true'])

    def test_double_forked_descendant_is_reparented_and_killed(self):
        # A real orphan (fork, setsid, fork, sleep) in a child process acting as the stage runner.
        code = r"""
import os, runpy, subprocess, sys, time
g = runpy.run_path(sys.argv[1])
g['become_subreaper']()
subprocess.run(['sh', '-c', 'setsid sh -c "sleep 300 & exit 0" & exit 0'], check=True)
deadline = time.time() + 5
while not g['children'](os.getpid()) and time.time() < deadline:
    time.sleep(0.05)
before = len(g['children'](os.getpid()))
g['kill_leftover_children']()
print(before, len(g['children'](os.getpid())))
"""
        result = subprocess.run(['python3', '-c', code, str(FACTORY / 'snitchwatch-system-stage.py')], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        before, after = map(int, result.stdout.split())
        self.assertGreaterEqual(before, 1)
        self.assertEqual(after, 0)

    def test_executable_and_parents_must_not_be_writable_by_the_unprivileged_user(self):
        binary = self.root / 'a/usr/bin/snitchwatch-bridge-cli'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        require = self.g['require_not_writable_by']
        require(binary, None)
        for target, mode in ((binary, 0o775), (binary.parent, 0o777), (binary, 0o757)):
            with self.subTest(str(target), mode=oct(mode)):
                target.chmod(mode)
                with self.assertRaises(ValueError):
                    require(binary, None)
                target.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'writable by uid'):
            require(binary, os.getuid())
        (self.root / 'link').symlink_to(binary)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            require(self.root / 'link', None)

    def stage_tree(self, root, files):
        for name, mode in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            path.chmod(mode)
        return root

    def test_adopt_copies_exactly_the_stage_sh_file_set(self):
        files = self.g['SYSTEM_STAGE_FILES']
        self.assertEqual(len(files), 9)
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        expected = {name.lstrip('/') for name, origin in verifier['SOURCE_FILES'].items() if origin.startswith(('packaging/system/', 'packaging/bluebuild/'))}
        self.assertEqual(set(files) - expected, {'usr/bin/snitchwatch-bridge-cli', 'etc/opensnitchd/default-config.json'})
        staged = self.stage_tree(self.root / 'staged', files)
        out = self.root / 'out'
        self.g['adopt_unprivileged_stage'](staged, out)
        for name, mode in files.items():
            self.assertEqual((out / name).read_bytes(), name.encode())
            self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), mode)
            self.assertEqual((out / name).stat().st_uid, os.geteuid())
        dirs = {str(p.relative_to(out)) for p in out.rglob('*') if p.is_dir()}
        self.assertTrue(all(stat.S_IMODE((out / d).stat().st_mode) == 0o755 for d in dirs))

    def test_adopt_refuses_anything_the_binary_could_have_planted(self):
        files = self.g['SYSTEM_STAGE_FILES']
        cases = {
            'extra unit': lambda s: (s / 'etc/systemd/system').mkdir(parents=True),
            'extra file': lambda s: (s / 'usr/lib/systemd/system/evil.service').write_text('x'),
            'symlink': lambda s: ((s / 'usr/lib/tmpfiles.d/snitchwatch.conf').unlink(), (s / 'usr/lib/tmpfiles.d/snitchwatch.conf').symlink_to('/etc/shadow')),
            'setuid': lambda s: (s / 'usr/bin/snitchwatch-bridge-cli').chmod(0o4755),
            'wrong mode': lambda s: (s / 'etc/opensnitchd/default-config.json').chmod(0o600),
            'hard link': lambda s: os.link(s / 'usr/lib/sysusers.d/snitchwatch.conf', self.root / ('hl-' + s.name)),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                staged = self.stage_tree(self.root / ('s-' + label.replace(' ', '-')), files)
                mutate(staged)
                with self.assertRaisesRegex(ValueError, 'unexpected path in unprivileged stage.sh output'):
                    self.g['adopt_unprivileged_stage'](staged, self.root / ('o-' + label.replace(' ', '-')))


if __name__ == '__main__':
    unittest.main()
