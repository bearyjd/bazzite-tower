#!/usr/bin/env python3
"""Behavioral build-selector and immutable system-manifest contracts.

Metadata fixtures deliberately do not execute a binary. Actual compilation,
schema1 verification, staging and reproducibility have separate container gates.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
FIXTURES = Path(__file__).with_name('test-snitchwatch-image-build-fixtures')
MANIFEST = 'usr/share/snitchwatch/system-bridge-manifest.json'
FETCH_RULE = 'etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json'
OS_UPDATE_RULE = 'etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json'
OS_UPDATE_SOURCE = FACTORY / 'snitchwatch-system-os-update-rule.json'
DNS_RULE = 'etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json'
DNS_SOURCE = FACTORY / 'snitchwatch-system-resolved-dns-rule.json'
EVENTS_DIR = 'usr/share/snitchwatch/endpoint-events/'
EVENTS_FILES = ('snitchwatch-endpoint-events.conf', 'snitchwatch-endpoint-events.nft',
                'snitchwatch-endpoint-events.service', 'snitchwatch-endpoint-events-nft.service')
sha = lambda data: hashlib.sha256(data).hexdigest()


def scanner_fixture(root, pins, inventory_sha):
    """Nonexecuted scanner build evidence for the real inventory verifier."""
    root.mkdir(parents=True, exist_ok=True)
    write = lambda name, data: (root / name).write_bytes(data if isinstance(data, bytes) else data.encode())
    names = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
    for name in names:
        write(name, b'\x7fELF\x02\x01' + bytes(58))
        (root / name).chmod(0o755)
        write(name + '.ldd', 'libc.so.6 => fixture\n')
    inputs = {name: pins['sourceFiles'][name] for name in ('Cargo.toml', 'Cargo.lock')}
    real = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())['sourceFiles']
    inputs.update({name: real[name] for name in real if name.startswith('crates/scanner-')})
    write('source-inputs.json', json.dumps(inputs))
    write('dependency-closure.json', json.dumps({name: [{'name': 'fixture', 'version': '1', 'license': 'MIT', 'source': 'fixture'}] for name in names}))
    for name in ('LICENSE', 'THIRD-PARTY-LICENSES.md', 'build.log', 'repro.log', 'test.log'):
        write(name, 'Nonexecuted fixture evidence\n')
    evidence = {path.name: sha(path.read_bytes()) for path in root.iterdir() if path.name not in names}
    write('provenance.json', json.dumps({
        'schemaVersion': 1, 'sourceCommit': pins['sourceCommit'], 'sourceTree': pins['sourceTree'],
        'cargoLockSha256': pins['sourceFiles']['Cargo.lock'], 'cargoTomlSha256': pins['sourceFiles']['Cargo.toml'],
        'builderImage': pins['builderImage'], 'rustcVv': 'rustc ' + pins['rustVersion'] + ' (fixture)',
        'rpmInventorySha256': inventory_sha, 'reproducible': True, 'testsPassed': True,
        'buildCommand': ['cargo', 'build', '--release', '--locked', '--offline', '-p', 'scanner-privileged', '-p', 'scanner-broker'],
        'binaries': {name: sha((root / name).read_bytes()) for name in names}, 'evidence': evidence}))


class ImageBuildContracts(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='sw-image-contract.')
        self.root = Path(self.work.name)
        self.addCleanup(self.work.cleanup)
        self.pins = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
        self.make_fixture()

    def write(self, name, data, mode=0o644):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        path.chmod(mode)

    def make_fixture(self):
        binary = 'usr/bin/snitchwatch-bridge-cli'
        # A nonexecuted ELF64 metadata fixture; no source-build claim attached.
        self.write(binary, b'\x7fELF\x02\x01' + bytes(58), 0o755)
        units = {'snitchwatch-system-bridge.service': 'usr/lib/systemd/system/',
                 'snitchwatch-system-bridge-grpc.socket': 'usr/lib/systemd/system/',
                 'snitchwatch-system-bridge-gui.socket': 'usr/lib/systemd/system/',
                 'snitchwatch.conf': 'usr/lib/tmpfiles.d/',
                 'snitchwatch.conf.sysusers': 'usr/lib/sysusers.d/',
                 'opensnitch-snitchwatch-system-bridge.conf': 'usr/lib/systemd/system/opensnitch.service.d/'}
        for name, directory in units.items():
            target = {'snitchwatch.conf.sysusers': 'snitchwatch.conf', 'opensnitch-snitchwatch-system-bridge.conf': 'snitchwatch-system-bridge.conf'}.get(name, name)
            self.write(directory + target, (FIXTURES / name).read_bytes())
        self.write(FETCH_RULE, (FIXTURES / '000-snitchwatch-bridge-fetch.json').read_bytes())
        self.write(OS_UPDATE_RULE, OS_UPDATE_SOURCE.read_bytes())
        self.write(DNS_RULE, DNS_SOURCE.read_bytes())
        for name in ['LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md']:
            self.write('usr/share/licenses/snitchwatch-bridge/' + name, 'License metadata fixture\n')
        self.write('usr/share/bazzite-tower/snitchwatch-bridge-profile', 'system\n')
        self.write('usr/share/bazzite-tower/opensnitchd-default-config.json', (ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-default-config.json').read_bytes())
        self.write('usr/share/bazzite-tower/opensnitchd-system-bridge-config.json', (ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json').read_bytes())
        self.write('usr/libexec/snitchwatch/verify-system-manifest.py', (FACTORY / 'snitchwatch-system-verify.py').read_bytes(), 0o755)
        self.write('usr/share/snitchwatch/build-pins.json', (FACTORY / 'snitchwatch-system-pins.json').read_bytes())
        inventory = 'rust-0:1.99.0-1.fc44.x86_64\n'
        self.write('usr/share/snitchwatch/native-rpm-inventory.txt', inventory)
        artifact = {'schema_version': 1, 'version': self.pins['bridgeVersion'], 'source': {'git_commit': self.pins['sourceCommit'], 'dirty': False},
                    'upstream': {'opensnitch_commit': self.pins['submoduleCommit']},
                    'build': {'builder_image': self.pins['builderImage'], 'rustc_vv': 'rustc ' + self.pins['rustVersion'] + ' (test)', 'protoc_version': 'libprotoc ' + self.pins['protocVersion']},
                    'files': [{'path': str(p.relative_to(self.root)), 'sha256': sha(p.read_bytes())} for p in sorted(self.root.rglob('*')) if p.is_file() and ('licenses' in p.parts or p == self.root / binary)]}
        self.write('usr/share/snitchwatch/schema1-artifact-MANIFEST.json', json.dumps(artifact))
        self.write('usr/share/snitchwatch/schema1-artifact-SHA256SUMS', 'Original artifact checksum metadata fixture\n')
        self.write('usr/share/snitchwatch/native-build-provenance.json', json.dumps({'sourceCommit': self.pins['sourceCommit'], 'submoduleCommit': self.pins['submoduleCommit'], 'sourceTree': self.pins['sourceTree'], 'reproducible': True, 'rpmInventorySha256': sha(inventory.encode())}))
        with tempfile.TemporaryDirectory(prefix='sw-scanner-fixture.') as temp:
            scanner = Path(temp)
            scanner_fixture(scanner, self.pins, sha(inventory.encode()))
            for path in scanner.iterdir():
                if path.name in ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker'):
                    destination, mode = 'usr/libexec/' + path.name, 0o755
                elif path.name in ('LICENSE', 'THIRD-PARTY-LICENSES.md'):
                    destination, mode = 'usr/share/licenses/snitchwatch-scanner/' + path.name, 0o644
                else:
                    destination, mode = 'usr/share/snitchwatch/scanner/' + path.name, 0o644
                self.write(destination, path.read_bytes(), mode)
        for name, directory in [('org.snitchwatch.Scanner.service', 'usr/share/dbus-1/services/'),
                                ('org.snitchwatch.scanner.policy', 'usr/share/polkit-1/actions/'),
                                ('snitchwatch-scanner.conf', 'usr/lib/tmpfiles.d/')]:
            self.write(directory + name, (FIXTURES / name).read_bytes())
        for name in EVENTS_FILES:
            self.write(EVENTS_DIR + name, (FIXTURES / name).read_bytes())
        mask = self.root / 'usr/lib/systemd/user/snitchwatch-bridge.service'
        mask.parent.mkdir(parents=True); mask.symlink_to('/dev/null')
        self.manifest = {'schemaVersion': 1, 'profile': 'system', 'source': {'commit': self.pins['sourceCommit'], 'submoduleCommit': self.pins['submoduleCommit'], 'tree': self.pins['sourceTree']}, 'binary': {'path': '/' + binary, 'sha256': sha((self.root / binary).read_bytes()), 'version': self.pins['bridgeVersion']},
                         'files': {'/' + str(p.relative_to(self.root)): sha(p.read_bytes()) for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()},
                         'symlinks': {'/usr/lib/systemd/user/snitchwatch-bridge.service': '/dev/null'},
                         'flatpak': {'appId': 'org.snitchwatch.Snitchwatch', 'profile': 'system', 'runtimeRef': self.pins['flatpakRuntimeRef'], 'sourceCommit': self.pins['sourceCommit'], 'manifestSha256': self.pins['sourceFiles']['packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'], 'finishArgs': self.pins['flatpakFinishArgs']}}
        self.save()

    def save(self):
        self.write(MANIFEST, json.dumps(self.manifest))

    def check(self, valid=False, live=False):
        # Default is an image/staged tree (--image-tree); live=True is the booted host's invocation.
        self.save()
        flags = [] if live else ['--image-tree']
        result = subprocess.run(['python3', str(FACTORY / 'snitchwatch-system-verify.py'), '--root', str(self.root), *flags], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if valid else 1, result.stdout + result.stderr)
        return result

    def update_hash(self, name):
        self.manifest['files']['/' + name] = sha((self.root / name).read_bytes())

    def test_valid_complete_fixture_is_read_only(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}
        result = self.check(valid=True)
        # 20 since r15: the tower's /usr/share reference copy of its own drop-in
        # is gone; upstream's pinned drop-in is the one daemon drop-in.  21 with
        # the tower's always-on OS-update rule (r15, owner decision 2026-10-17),
        # 22 with its companion resolver DNS rule, plus 15 scanner files, plus 4
        # opt-in endpoint-events files (shipped inert under /usr/share).
        self.assertEqual(json.loads(result.stdout)['verifiedFiles'], 41)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()})

    def test_binary_tamper(self):
        self.write('usr/bin/snitchwatch-bridge-cli', 'tamper', 0o755)
        self.check()

    def test_scanner_inventory_rejects_missing_tampered_modes_and_links(self):
        for name in ('usr/libexec/snitchwatch-scanner-privileged',
                     'usr/libexec/snitchwatch-scanner-broker',
                     'usr/share/dbus-1/services/org.snitchwatch.Scanner.service',
                     'usr/share/polkit-1/actions/org.snitchwatch.scanner.policy',
                     'usr/lib/tmpfiles.d/snitchwatch-scanner.conf'):
            with self.subTest(path=name):
                path = self.root / name
                data, mode = path.read_bytes(), path.stat().st_mode & 0o777
                path.unlink(); self.check()
                self.write(name, data, mode)
                path.chmod(0o777); self.check()
                path.chmod(mode)
                path.unlink(); path.symlink_to('/dev/null'); self.check()
                path.unlink(); self.write(name, data + b'changed', mode); self.check()
                self.write(name, data, mode)
        self.check(valid=True)

    def test_endpoint_events_inventory_rejects_missing_tampered_modes_and_links(self):
        for base in EVENTS_FILES:
            name = EVENTS_DIR + base
            with self.subTest(path=name):
                path = self.root / name
                data, mode = path.read_bytes(), path.stat().st_mode & 0o777
                path.unlink(); self.check()
                self.write(name, data, mode)
                path.chmod(0o755); self.check()
                path.chmod(mode)
                path.unlink(); path.symlink_to('/dev/null'); self.check()
                path.unlink(); self.write(name, data + b'changed', mode); self.check()
                self.write(name, data, mode)
        self.check(valid=True)

    def test_endpoint_events_files_are_pinned_source_inert_under_usr_share(self):
        for base in EVENTS_FILES:
            self.assertEqual(sha((FIXTURES / base).read_bytes()), self.pins['sourceFiles']['packaging/endpoint-events/' + base])
        self.check(valid=True)

    def test_endpoint_events_units_are_never_enabled_or_in_a_unit_path(self):
        def link(name):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to('/usr/share/snitchwatch/endpoint-events/snitchwatch-endpoint-events.service')
        cases = {
            'usr/lib/systemd/system/multi-user.target.wants/snitchwatch-endpoint-events.service': link,
            'usr/lib/systemd/system/multi-user.target.wants/snitchwatch-endpoint-events-nft.service': link,
            'etc/systemd/system/multi-user.target.wants/snitchwatch-endpoint-events.service': link,
            'etc/systemd/system/multi-user.target.requires/snitchwatch-endpoint-events-nft.service': link,
            'usr/lib/systemd/system/snitchwatch-endpoint-events.service': lambda n: self.write(n, '[Service]\n'),
            'etc/systemd/system/snitchwatch-endpoint-events-nft.service': lambda n: self.write(n, '[Service]\n'),
        }
        for name, create in cases.items():
            with self.subTest(case=name):
                create(name)
                self.assertIn('endpoint-events', self.check().stderr)
                (self.root / name).unlink()
        self.check(valid=True)

    def test_endpoint_events_etc_state_is_operator_state_on_a_live_host(self):
        # The verifier also runs on the booted host (bridge helper, no flag); an
        # operator following the opt-in README must not make it refuse.  The
        # image-owned /usr tree is still refused there; /etc only with --image-tree.
        name = 'etc/systemd/system/multi-user.target.wants/snitchwatch-endpoint-events.service'
        self.write('etc/systemd/system/snitchwatch-endpoint-events.service', '[Service]\n')
        (self.root / name).parent.mkdir(parents=True)
        (self.root / name).symlink_to('/etc/systemd/system/snitchwatch-endpoint-events.service')
        self.assertIn('endpoint-events', self.check().stderr)
        self.check(valid=True, live=True)
        usr = 'usr/lib/systemd/system/multi-user.target.wants/snitchwatch-endpoint-events.service'
        (self.root / usr).parent.mkdir(parents=True)
        (self.root / usr).symlink_to('/dev/null')
        self.assertIn('endpoint-events', self.check(live=True).stderr)

    def test_operator_saved_logger_copy_in_etc_does_not_break_a_live_host(self):
        self.write('etc/opensnitchd/opensnitchd-logger.json', '{"name": "remote"}')
        self.assertIn('opensnitchd-logger', self.check().stderr)
        self.check(valid=True, live=True)
        self.write('usr/share/snitchwatch/endpoint-events/opensnitchd-logger.json', '{}')
        self.assertIn('opensnitchd-logger', self.check(live=True).stderr)

    def test_endpoint_events_preset_pins_stock_rsyslog_off(self):
        # RPM %post and first-boot `systemctl preset-all` apply Fedora's
        # `enable rsyslog.*`; the image preset (first match wins) must beat it.
        path = ROOT / 'system_files/usr/lib/systemd/system-preset/00-bazzite-tower-endpoint-events.preset'
        self.assertTrue(path.is_file() and not path.is_symlink())
        self.assertEqual(path.stat().st_mode & 0o7777, 0o644)
        lines = [l for l in path.read_text().splitlines() if l and not l.startswith('#')]
        self.assertIn('disable rsyslog.service', lines)
        self.assertTrue(all(l.startswith('disable ') for l in lines), lines)

    def test_no_logger_entry_and_no_logger_snippet_anywhere(self):
        for name in ('usr/share/bazzite-tower/opensnitchd-system-bridge-config.json',):
            config = json.loads((self.root / name).read_text())
            self.assertNotIn('Loggers', config['Server'])
        for name in ('usr/share/bazzite-tower/opensnitchd-system-bridge-config.json',
                     'usr/share/bazzite-tower/opensnitchd-default-config.json'):
            with self.subTest(path=name):
                original = (self.root / name).read_bytes()
                config = json.loads(original)
                config['Server']['Loggers'] = [{'name': 'remote', 'format': 'json', 'server': 'tcp://127.0.0.1:514'}]
                self.write(name, json.dumps(config)); self.update_hash(name)
                self.assertIn('Loggers', self.check().stderr)
                self.write(name, original); self.update_hash(name)
        for name in ('etc/opensnitchd/opensnitchd-logger.json', 'usr/share/snitchwatch/endpoint-events/opensnitchd-logger.json'):
            with self.subTest(path=name):
                self.write(name, '{"name": "remote"}')
                self.assertIn('opensnitchd-logger', self.check().stderr)
                (self.root / name).unlink()
        self.check(valid=True)

    def test_scanner_provenance_and_source_refusals(self):
        name = 'usr/share/snitchwatch/scanner/provenance.json'
        path = self.root / name
        original = json.loads(path.read_text())
        for key, value in [('sourceCommit', '0' * 40), ('sourceTree', '0' * 40),
                           ('reproducible', False), ('testsPassed', False),
                           ('binaries', {}), ('evidence', {}),
                           ('cargoLockSha256', '0' * 64), ('rpmInventorySha256', '0' * 64)]:
            with self.subTest(field=key):
                self.write(name, json.dumps({**original, key: value}))
                self.update_hash(name)
                self.check()
        self.write(name, json.dumps(original)); self.update_hash(name)
        self.check(valid=True)

    def test_scanner_has_no_persistent_systemd_activation(self):
        self.write('usr/lib/systemd/system/snitchwatch-scanner.service', '[Service]\nExecStart=/usr/libexec/snitchwatch-scanner-privileged\n')
        self.check()

    def test_scanner_persistent_activation_refusals_are_broad(self):
        def link(name):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to('/dev/null')
        def file(name, text):
            self.write(name, text)
        binary = '[Service]\nExecStart=/usr/libexec/snitchwatch-scanner-%s\n'
        cases = {
            'usr/lib/systemd/system/dbus-org.snitchwatch.Scanner.service': lambda n: link(n),
            'etc/systemd/system/SnitchWatch-SCANNER.timer': lambda n: file(n, '[Timer]\n'),
            'usr/lib/systemd/user/deep-scanner.path': lambda n: file(n, '[Path]\n'),
            'etc/systemd/system/multi-user.target.wants/scanner-run.service': lambda n: link(n),
            'usr/lib/systemd/system/other.service': lambda n: file(n, binary % 'broker'),
            'usr/share/dbus-1/system-services/org.example.Thing.service': lambda n: file(n, binary % 'privileged'),
            'usr/share/dbus-1/services/org.example.Thing.service': lambda n: file(n, binary % 'broker'),
        }
        for name, create in cases.items():
            with self.subTest(case=name):
                create(name)
                self.assertIn('persistent scanner activation', self.check().stderr)
                (self.root / name).unlink()
        self.check(valid=True)

    def scanner_inputs(self, mutate):
        name = 'usr/share/snitchwatch/scanner/source-inputs.json'
        inputs = json.loads((self.root / name).read_text())
        mutate(inputs)
        self.write(name, json.dumps(inputs))
        self.update_hash(name)
        provenance_name = 'usr/share/snitchwatch/scanner/provenance.json'
        provenance = json.loads((self.root / provenance_name).read_text())
        provenance['evidence']['source-inputs.json'] = sha((self.root / name).read_bytes())
        self.write(provenance_name, json.dumps(provenance))
        self.update_hash(provenance_name)

    def test_scanner_source_inputs_refuse_unexpected_and_changed_files(self):
        self.scanner_inputs(lambda inputs: inputs.update({'crates/scanner-privileged/build.rs': '0' * 64}))
        self.assertIn('source input', self.check().stderr)

    def test_scanner_source_inputs_refuse_changed_crate_manifest_and_cargo_config(self):
        self.scanner_inputs(lambda inputs: inputs.update({'crates/scanner-broker/Cargo.toml': '0' * 64}))
        self.assertIn('source input', self.check().stderr)
        self.scanner_inputs(lambda inputs: inputs.update({'crates/scanner-broker/Cargo.toml': self.pins['sourceFiles']['crates/scanner-broker/Cargo.toml'], '.cargo/config.toml': '0' * 64}))
        self.assertIn('source input', self.check().stderr)
        self.scanner_inputs(lambda inputs: inputs.pop('.cargo/config.toml'))
        self.check(valid=True)

    def git_source(self, tracked):
        source = self.root / 'git-source'
        for name, text in tracked.items():
            self.write(str(source.relative_to(self.root) / name), text)
        env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        for command in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(source), *command], check=True, env=env)
        return source

    def test_scanner_bridge_module_load_leaves_source_checkout_clean(self):
        source = self.git_source({'packaging/release/bridge_artifact.py': 'def crate_key(p):\n    return p\n'})
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONDONTWRITEBYTECODE'}
        code = ('import runpy,sys,pathlib; sys.dont_write_bytecode=False; '
                'h=runpy.run_path(sys.argv[1]); m=h["load_bridge_artifact"](pathlib.Path(sys.argv[2])); assert m.crate_key(1)==1')
        subprocess.run(['python3', '-c', code, str(FACTORY / 'snitchwatch-system-scanner-build.py'), str(source)], check=True, env=env)
        status = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--ignored'], text=True)
        self.assertEqual(status, '')

    def test_scanner_environment_disables_bytecode_for_children(self):
        helper = runpy.run_path(str(FACTORY / 'snitchwatch-system-scanner-build.py'))
        self.assertEqual(helper['clean_environment']({'PATH': '/usr/bin'})['PYTHONDONTWRITEBYTECODE'], '1')

    def test_scanner_build_only_reads_the_source_checkout(self):
        text = (FACTORY / 'snitchwatch-system-scanner-build.py').read_text()
        uses = [re.sub(r'\s+', ' ', line.split('#')[0]).strip() for line in text.splitlines() if re.search(r'(?<![\w\'".-])source\b(?![\'".])', line.split('#')[0])]
        allowed = ("source, out = args.source.resolve(), args.out.resolve()",
                   "subprocess.run(['git', '-C', str(source), 'archive', pins['sourceCommit']], stdout=output, check=True)",
                   "env['SOURCE_DATE_EPOCH'] = subprocess.check_output(['git', '-C', str(source), 'log', '-1', '--format=%ct'], text=True).strip()",
                   "shutil.copyfile(source / 'LICENSE', out / 'LICENSE')",
                   "artifact = load_bridge_artifact(source)", "def load_bridge_artifact(source):",
                   "spec = importlib.util.spec_from_file_location('artifact', source / 'packaging/release/bridge_artifact.py')",
                   "for name in ('source', 'out', 'pins', 'rpm-inventory'):",
                   "write_json(out / 'source-inputs.json', {str(path.relative_to(exported)): sha(path)",
                   "parser.add_argument('--' + name, required=True, type=Path)")
        extra = [line for line in uses if line not in allowed and not line.startswith(('\"\"\"', 'The caller', "'sourceIdentity'"))]
        self.assertEqual(extra, [])
        self.assertIn('sys.dont_write_bytecode = True', text)

    def stage_check(self, source, commit, tree):
        helper = runpy.run_path(str(FACTORY / 'snitchwatch-system-stage.py'))
        helper['require_exact_clean_source'](source, {'sourceCommit': commit, 'sourceTree': tree})

    def test_stage_source_check_reports_dirty_paths_and_mismatch(self):
        source = self.git_source({'a.txt': 'a\n'})
        git = lambda *v: subprocess.check_output(['git', '-C', str(source), *v], text=True).strip()
        commit, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
        self.stage_check(source, commit, tree)
        (source / 'pkg/__pycache__').mkdir(parents=True)
        (source / 'pkg/__pycache__/x.pyc').write_bytes(b'x')
        (source / 'pkg/.gitignore').write_text('__pycache__/\n')
        with self.assertRaises(ValueError) as caught:
            self.stage_check(source, commit, tree)
        self.assertIn('pkg/', str(caught.exception))
        (source / 'pkg/.gitignore').unlink(); shutil.rmtree(source / 'pkg')
        with self.assertRaises(ValueError) as caught:
            self.stage_check(source, '0' * 40, tree)
        self.assertIn('HEAD', str(caught.exception))

    def test_scanner_private_bus_step_must_have_run_exactly_one_test(self):
        helper = runpy.run_path(str(FACTORY / 'snitchwatch-system-scanner-build.py'))
        check = helper['require_private_bus_test']
        ok = 'test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 9 filtered out\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 3 filtered out\n'
        check(ok)
        for bad in ('', 'test result: ok. 0 passed; 0 failed; 1 ignored; 0 measured; 10 filtered out\n',
                    'test result: ok. 2 passed; 0 failed\n', ok + 'test result: FAILED. 0 passed; 1 failed\n'):
            with self.subTest(log=bad[:40]), self.assertRaises(ValueError):
                check(bad)

    def test_scanner_packaging_fixtures_match_pinned_source(self):
        for fixture, source in [('scanner-stage.sh', 'packaging/scanner/stage.sh'),
                                ('org.snitchwatch.Scanner.service', 'packaging/scanner/org.snitchwatch.Scanner.service'),
                                ('snitchwatch-scanner.conf', 'packaging/scanner/snitchwatch-scanner.conf'),
                                ('org.snitchwatch.scanner.policy', 'packaging/polkit/org.snitchwatch.scanner.policy')]:
            self.assertEqual(sha((FIXTURES / fixture).read_bytes()), self.pins['sourceFiles'][source])
        policy = (FIXTURES / 'org.snitchwatch.scanner.policy').read_text()
        self.assertIn('<allow_active>auth_admin_keep</allow_active>', policy)
        self.assertIn('<allow_inactive>no</allow_inactive>', policy)
        self.assertIn('<allow_any>no</allow_any>', policy)

    def test_scanner_is_built_from_upstream_without_a_carried_patch(self):
        self.assertNotIn('scannerPatch', self.pins)
        self.assertEqual(list(FACTORY.glob('*scanner*.patch')), [])
        for name in ('command', 'facts', 'lib', 'main'):
            self.assertRegex(self.pins['sourceFiles']['crates/scanner-privileged/src/%s.rs' % name], '^[0-9a-f]{64}$')
        builder = (FACTORY / 'snitchwatch-system-scanner-build.py').read_text()
        for needle in ('apply', 'scannerPatch', '--patch', '.patch', 'patchedFiles'):
            self.assertNotIn(needle, builder)
        self.assertNotIn('patch', (FACTORY / 'snitchwatch-system-build.sh').read_text().lower())

    def test_scanner_provenance_and_inputs_refuse_drift(self):
        for src in ('command', 'facts', 'lib', 'main'):
            key = 'crates/scanner-privileged/src/%s.rs' % src
            with self.subTest(source=src):
                self.scanner_inputs(lambda inputs: inputs.update({key: '0' * 64}))
                self.assertIn('differs from pinned upstream', self.check().stderr)
                self.scanner_inputs(lambda inputs: inputs.update({key: self.pins['sourceFiles'][key]}))
        self.check(valid=True)

    def test_scanner_builder_refuses_exported_files_that_differ_from_pins(self):
        helper = runpy.run_path(str(FACTORY / 'snitchwatch-system-scanner-build.py'))
        tree = self.root / 'exported'
        pins = {'sourceFiles': {}}
        for name, content in (('crates/scanner-privileged/src/main.rs', b'main'), ('crates/scanner-privileged/src/command.rs', b'cmd'),
                              ('Cargo.toml', b'toml')):
            self.write('exported/' + name, content)
            pins['sourceFiles'][name] = sha(content)
        helper['require_pinned_scanner_sources'](tree, pins)
        self.write('exported/crates/scanner-privileged/src/command.rs', b'tampered')
        with self.assertRaisesRegex(ValueError, 'differs from pinned upstream'):
            helper['require_pinned_scanner_sources'](tree, pins)

    def test_scanner_builder_discards_toolchain_override_environment(self):
        helper = runpy.run_path(str(FACTORY / 'snitchwatch-system-scanner-build.py'))
        keys = ('RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'CARGO_BUILD_RUSTC_WRAPPER',
                'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER', 'RUSTC', 'RUSTDOC', 'CARGO_BUILD_RUSTC',
                'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER',
                'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS', 'CARGO_BUILD_RUSTFLAGS',
                'CARGO_ENCODED_RUSTFLAGS', 'RUSTFLAGS')
        environment = {key: 'untrusted fixture executable' for key in keys}
        environment.update(CARGO_HOME='/isolated/cargo', PATH='/usr/bin')
        self.assertEqual(helper['clean_environment'](environment), {'CARGO_HOME': '/isolated/cargo', 'PATH': '/usr/bin', 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_incomplete_inventory(self):
        del self.manifest['files']['/usr/lib/tmpfiles.d/snitchwatch.conf']
        self.check()

    def test_unexpected_inventory_cannot_read_etc(self):
        self.manifest['files']['/etc/shadow'] = '0' * 64
        result = self.check()
        self.assertIn('inventory', result.stderr)

    def test_fetch_rule_fixture_is_the_pinned_source(self):
        pinned = self.pins['sourceFiles']['packaging/bluebuild/files/system/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json']
        self.assertEqual(sha((FIXTURES / '000-snitchwatch-bridge-fetch.json').read_bytes()), pinned)
        self.assertEqual(self.manifest['files']['/' + FETCH_RULE], pinned)

    def test_read_file_allows_only_the_reserved_rules_under_etc(self):
        # Was "only the fetch rule"; r15 reserves exactly two more /etc paths,
        # the tower OS-update and resolver DNS rules.  Everything else under
        # /etc still refuses.
        module = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        self.write('etc/opensnitchd/rules/other.json', '{}')
        self.write('etc/shadow', 'x')
        for name in ['/etc/shadow', '/etc/opensnitchd/rules/other.json', '/etc/opensnitchd/rules/../rules/000-snitchwatch-bridge-fetch.json',
                     '/etc/opensnitchd/rules/../rules/000-snitchwatch-tower-os-update.json',
                     '/etc/opensnitchd/rules/../rules/000-snitchwatch-tower-resolved-dns.json', '/usr/../etc/shadow']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'invalid immutable file path'):
                module['read_file'](self.root, name)
        self.assertEqual(module['read_file'](self.root, '/' + FETCH_RULE), (self.root / FETCH_RULE).read_bytes())
        self.assertEqual(module['read_file'](self.root, '/' + OS_UPDATE_RULE), (self.root / OS_UPDATE_RULE).read_bytes())
        self.assertEqual(module['read_file'](self.root, '/' + DNS_RULE), (self.root / DNS_RULE).read_bytes())
        self.assertEqual(module['RESERVED_RULES'], {'/' + FETCH_RULE, '/' + OS_UPDATE_RULE, '/' + DNS_RULE})

    def test_os_update_rule_source_is_the_pinned_digest(self):
        module = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        self.assertEqual(sha(OS_UPDATE_SOURCE.read_bytes()), module['OS_UPDATE_RULE_SHA256'])
        self.assertEqual(self.manifest['files']['/' + OS_UPDATE_RULE], module['OS_UPDATE_RULE_SHA256'])

    def test_os_update_rule_is_narrow_and_cannot_shadow_deny(self):
        rule = json.loads(OS_UPDATE_SOURCE.read_text())
        self.assertEqual(rule['name'], Path(OS_UPDATE_RULE).stem)
        self.assertEqual((rule['action'], rule['duration'], rule['enabled']), ('allow', 'always', True))
        # precedence false: FindFirstMatch keeps scanning, so any matching
        # deny/reject rule (blocklists included) still wins over this allow.
        self.assertIs(rule['precedence'], False)
        op = rule['operator']
        self.assertEqual((op['operand'], op['type']), ('list', 'list'))
        terms = {term['operand']: (term['type'], term['data'], term['sensitive']) for term in op['list']}
        self.assertEqual(len(terms), len(op['list']))
        self.assertEqual(terms, {
            'process.path': ('regexp', '^/usr/bin/(skopeo|rpm-ostree)$', True),
            'user.id': ('regexp', '^(0|65534)$', False),
            'dest.port': ('simple', '443', False),
            'protocol': ('regexp', '^tcp6?$', False),
        })

    def test_self_consistent_os_update_rule_edit(self):
        # Manifest updated to match: only the verifier's pinned digest catches it.
        rule = json.loads((self.root / OS_UPDATE_RULE).read_text())
        rule['operator']['list'] = [op for op in rule['operator']['list'] if op['operand'] != 'user.id']
        self.write(OS_UPDATE_RULE, json.dumps(rule)); self.update_hash(OS_UPDATE_RULE)
        result = self.check()
        self.assertIn('tower rule differs from the reviewed digest: /' + OS_UPDATE_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_os_update_rule_edit_without_manifest_update(self):
        self.write(OS_UPDATE_RULE, (self.root / OS_UPDATE_RULE).read_text().replace('"precedence": false', '"precedence": true'))
        self.assertIn('immutable file hash mismatch: /' + OS_UPDATE_RULE, self.check().stderr)

    def test_missing_os_update_rule(self):
        (self.root / OS_UPDATE_RULE).unlink()
        result = self.check()
        self.assertIn('missing immutable file: /' + OS_UPDATE_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_manifest_without_os_update_rule(self):
        del self.manifest['files']['/' + OS_UPDATE_RULE]
        self.assertIn('inventory', self.check().stderr)

    def test_tower_rules_use_the_bridges_reserved_packaged_prefix(self):
        # Snitchwatch 7bf3f19 shows every 000-snitchwatch-* rule read-only and
        # refuses GUI change/toggle/delete of it; only the fetch rule's exact
        # name is its own, so the tower names must differ from that one.
        for source, path in ((OS_UPDATE_SOURCE, OS_UPDATE_RULE), (DNS_SOURCE, DNS_RULE)):
            name = json.loads(source.read_text())['name']
            self.assertTrue(name.startswith('000-snitchwatch-tower-'), name)
            self.assertNotEqual(name, '000-snitchwatch-bridge-fetch')
            self.assertEqual(Path(path).name, name + '.json')

    def test_dns_rule_source_is_the_pinned_digest(self):
        module = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        self.assertEqual(sha(DNS_SOURCE.read_bytes()), module['DNS_RULE_SHA256'])
        self.assertEqual(self.manifest['files']['/' + DNS_RULE], module['DNS_RULE_SHA256'])

    def test_dns_rule_is_narrow_and_cannot_shadow_deny(self):
        rule = json.loads(DNS_SOURCE.read_text())
        self.assertEqual(rule['name'], Path(DNS_RULE).stem)
        self.assertEqual((rule['action'], rule['duration'], rule['enabled']), ('allow', 'always', True))
        self.assertIs(rule['precedence'], False)
        op = rule['operator']
        self.assertEqual((op['operand'], op['type']), ('list', 'list'))
        terms = {term['operand']: (term['type'], term['data'], term['sensitive']) for term in op['list']}
        self.assertEqual(len(terms), len(op['list']))
        self.assertEqual(terms, {
            'process.path': ('simple', '/usr/lib/systemd/systemd-resolved', True),
            'user.id': ('regexp', '^([0-9]{1,2}|[1-9][0-9]{2})$', False),
            'dest.port': ('regexp', '^(53|853)$', False),
            'protocol': ('regexp', '^(udp|udp6|tcp|tcp6)$', False),
        })

    def test_dns_rule_matches_only_system_uids_and_dns_ports(self):
        # OpenSnitch matches user.id against strconv.Itoa(uid) and dest.port
        # against its decimal string (operator.go at b404c4c); Python's re
        # agrees with Go RE2 on these anchored patterns.
        terms = {term['operand']: re.compile(term['data']) for term in json.loads(DNS_SOURCE.read_text())['operator']['list'] if term['type'] == 'regexp'}
        for uid in ('0', '9', '10', '99', '100', '193', '990', '999'):
            self.assertTrue(terms['user.id'].search(uid), uid)
        for uid in ('1000', '1001', '65534', '524288', '-1', ''):
            self.assertFalse(terms['user.id'].search(uid), uid)
        for port in ('53', '853'):
            self.assertTrue(terms['dest.port'].search(port), port)
        for port in ('5353', '5355', '443', '8853', '530', '3'):
            self.assertFalse(terms['dest.port'].search(port), port)
        for proto in ('udp', 'udp6', 'tcp', 'tcp6'):
            self.assertTrue(terms['protocol'].search(proto), proto)
        for proto in ('icmp', 'udplite', 'sctp'):
            self.assertFalse(terms['protocol'].search(proto), proto)

    def test_self_consistent_dns_rule_edit(self):
        rule = json.loads((self.root / DNS_RULE).read_text())
        rule['operator']['list'] = [op for op in rule['operator']['list'] if op['operand'] != 'user.id']
        self.write(DNS_RULE, json.dumps(rule)); self.update_hash(DNS_RULE)
        result = self.check()
        self.assertIn('tower rule differs from the reviewed digest: /' + DNS_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_missing_dns_rule(self):
        (self.root / DNS_RULE).unlink()
        result = self.check()
        self.assertIn('missing immutable file: /' + DNS_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_manifest_without_dns_rule(self):
        del self.manifest['files']['/' + DNS_RULE]
        self.assertIn('inventory', self.check().stderr)

    def test_dns_rule_mode_0600(self):
        (self.root / DNS_RULE).chmod(0o600)
        self.assertIn('incorrect file type/mode: /' + DNS_RULE, self.check().stderr)

    def test_os_update_rule_symlinked(self):
        path = self.root / OS_UPDATE_RULE
        copy = self.root / 'elsewhere/000-snitchwatch-tower-os-update.json'
        copy.parent.mkdir(); copy.write_bytes(path.read_bytes()); copy.chmod(0o644)
        path.unlink(); path.symlink_to(copy)
        self.assertIn('symlink in immutable file path: /' + OS_UPDATE_RULE, self.check().stderr)

    def test_self_consistent_fetch_rule_edit(self):
        # Manifest updated to match: only the pinned-source comparison can catch it.
        rule = json.loads((self.root / FETCH_RULE).read_text())
        rule['operator']['list'] = [op for op in rule['operator']['list'] if op['operand'] != 'dest.port']
        self.write(FETCH_RULE, json.dumps(rule)); self.update_hash(FETCH_RULE)
        result = self.check()
        self.assertIn('system overlay differs from pinned source: /' + FETCH_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_fetch_rule_edit_without_manifest_update(self):
        self.write(FETCH_RULE, (self.root / FETCH_RULE).read_text().replace('"precedence": false', '"precedence": true'))
        result = self.check()
        self.assertIn('immutable file hash mismatch: /' + FETCH_RULE, result.stderr)

    def test_missing_fetch_rule(self):
        (self.root / FETCH_RULE).unlink()
        result = self.check()
        self.assertIn('missing immutable file: /' + FETCH_RULE, result.stderr)
        self.assertIn('reserved', result.stderr)

    def test_fetch_rule_mode_0600(self):
        (self.root / FETCH_RULE).chmod(0o600)
        result = self.check()
        self.assertIn('incorrect file type/mode: /' + FETCH_RULE, result.stderr)

    def test_fetch_rule_symlink_to_identical_copy(self):
        path = self.root / FETCH_RULE
        copy = self.root / 'elsewhere/000-snitchwatch-bridge-fetch.json'
        copy.parent.mkdir(); copy.write_bytes(path.read_bytes()); copy.chmod(0o644)
        path.unlink(); path.symlink_to(copy)
        result = self.check()
        self.assertIn('symlink in immutable file path: /' + FETCH_RULE, result.stderr)

    def test_fetch_rule_symlinked_rules_directory(self):
        rules = self.root / 'etc/opensnitchd/rules'
        moved = self.root / 'elsewhere-rules'
        rules.rename(moved); rules.symlink_to(moved)
        result = self.check()
        # All reserved rules live there; whichever is read first must refuse.
        self.assertRegex(result.stderr, 'symlink in immutable file path: /(' + '|'.join((FETCH_RULE, OS_UPDATE_RULE, DNS_RULE)) + ') ')

    def test_self_consistent_hardening_tamper(self):
        name = 'usr/lib/systemd/system/snitchwatch-system-bridge.service'
        self.write(name, (self.root / name).read_text().replace('NoNewPrivileges=true', 'NoNewPrivileges=false'))
        self.update_hash(name)
        self.check()

    def test_source_forgery(self):
        self.manifest['source']['commit'] = '0' * 40
        self.check()

    def test_self_consistent_tree_forgery(self):
        name = 'usr/share/snitchwatch/native-build-provenance.json'
        provenance = json.loads((self.root / name).read_text())
        provenance['sourceTree'] = '0' * 40
        self.write(name, json.dumps(provenance)); self.update_hash(name)
        self.manifest['source']['tree'] = '0' * 40
        self.check()

    def test_self_consistent_pin_forgery(self):
        name = 'usr/share/snitchwatch/build-pins.json'
        self.pins['sourceCommit'] = '0' * 40
        self.write(name, json.dumps(self.pins)); self.update_hash(name)
        self.manifest['source']['commit'] = '0' * 40
        self.check()

    def test_rust_pin_authentication(self):
        name = 'usr/share/snitchwatch/build-pins.json'
        for field, value in [('sha256', '0' * 64), ('size', 1),
                             ('nevra', 'rust-0:1.98.1-1.fc44.x86_64'),
                             ('kojiPath', 'unsigned/rust.rpm')]:
            with self.subTest(field=field):
                pins = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
                pins['toolchainRpms'][0][field] = value
                self.write(name, json.dumps(pins)); self.update_hash(name)
                self.assertIn('build pins differ from reviewed integration inputs', self.check().stderr)

    def test_reviewed_rust_rpm_inputs(self):
        daemon = json.loads((FACTORY / 'snitchwatch-system-daemon-pins.json').read_text())
        for key in ('rpmSigningKey', 'kojiBase'):
            self.assertEqual(self.pins[key], daemon[key])
        expected = {
            'rust': ('cbbedd9794b017a3b654382170c55a4ba75dcfdbb7a828d3dd63d0c4b93e01be', 32479878),
            'cargo': ('133d5003f7b30708a9143bd63ba4f2848dae5b1d9768dca7c4d2f65497af7206', 8270638),
            'rust-std-static': ('5b1753c706960ec4a6b2158cb5525e677111cff681c6b54c88f95f0f0fbe4202', 42781168),
        }
        rpms = self.pins['toolchainRpms']
        self.assertEqual(len(rpms), 3)
        self.assertEqual({r['name'] for r in rpms}, set(expected))
        for rpm in rpms:
            name = rpm['name']
            filename = f'{name}-1.99.0-1.fc44.x86_64.rpm'
            self.assertEqual((rpm['sha256'], rpm['size']), expected[name])
            self.assertEqual(rpm['file'], filename)
            self.assertEqual(rpm['nevra'], f'{name}-0:1.99.0-1.fc44.x86_64')
            self.assertEqual(rpm['kojiPath'], 'rust/1.99.0/1.fc44/data/signed/6d9f90a6/x86_64/' + filename)
        installer = (FACTORY / 'snitchwatch-system-install-deps.sh').read_bytes()
        self.assertEqual(sha(installer), '247a396c0f3d504023f001c33bb1211f5f0f9bfd2c6685077d4bd312ac95f8cb')

    def test_rust_install_precedes_version_gate(self):
        # Execute the actual setup fragment with transaction/tool stubs. The gate
        # succeeds only after the helper, and a helper failure must stop it.
        script = (FACTORY / 'snitchwatch-system-build.sh').read_text()
        fragment = script[script.index('bash "$factory/snitchwatch-system-install-deps.sh"'):
                          script.index('git init "$work/source"')]
        fragment = fragment.replace('/work', str(self.root / 'build-work'))
        stubs = r'''set -euo pipefail
bash() {
    case "$1" in
        */snitchwatch-system-install-deps.sh) echo upstream >> "$events" ;;
        */snitchwatch-system-toolchain.sh)
            [[ "$(cat "$events")" == $'upstream\ncurl' && "$2" == install ]]
            [[ "$3" == "$factory/snitchwatch-system-pins.json" && -d "${4%/toolchain}" ]]
            echo helper >> "$events"
            [[ "$fail_helper" == 0 ]] || return 19
            touch "$installed" ;;
        *) return 99 ;;
    esac
}
command() {
    [[ "$*" != '-v curl' ]] || return 1
    builtin command "$@"
}
timeout() { [[ "$1" == 240 ]]; shift; "$@"; }
dnf() {
    [[ "$*" == '-y --setopt=install_weak_deps=False install curl' ]]
    echo curl >> "$events"
}
rustc() {
    echo rustc >> "$events"
    [[ -f "$installed" ]] || return 1
    echo 'rustc 1.99.0 (fixture)'
}
protoc() { echo 'libprotoc 3.19.6'; }
'''
        for failure in (0, 1):
            with self.subTest(helper_failure=failure):
                events = self.root / f'events-{failure}'
                env = {**os.environ, 'factory': str(FACTORY), 'events': str(events),
                       'installed': str(self.root / f'installed-{failure}'), 'fail_helper': str(failure)}
                result = subprocess.run(['bash', '-c', stubs + fragment], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 19 if failure else 0, result.stderr)
                self.assertEqual(events.read_text().splitlines(),
                                 ['upstream', 'curl', 'helper'] + ([] if failure else ['rustc']))

    def test_self_consistent_dropin_requiring_its_directory(self):
        # Without the '-' a missing /run/snitchwatch would keep the
        # daemon from starting; the drop-in is pinned to upstream's bytes.
        name = 'usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf'
        self.write(name, (self.root / name).read_text().replace('WorkingDirectory=-/run', 'WorkingDirectory=/run')); self.update_hash(name)
        self.assertIn('system overlay differs from pinned source: /' + name, self.check().stderr)

    def test_self_consistent_policy_drift(self):
        name = 'usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'
        config = json.loads((self.root / name).read_text())
        config['DefaultAction'] = 'deny'
        self.write(name, json.dumps(config)); self.update_hash(name)
        self.assertIn('system candidate must retain the allow/proc defaults', self.check().stderr)

    def test_world_writable_token_parent_configuration(self):
        name = 'usr/lib/tmpfiles.d/snitchwatch.conf'
        self.write(name, (self.root / name).read_text().replace('2750', '2777')); self.update_hash(name)
        self.check()

    def test_unsafe_file_mode(self):
        (self.root / 'usr/bin/snitchwatch-bridge-cli').chmod(0o777)
        self.check()

    def test_symlink_escape(self):
        path = self.root / 'usr/share/snitchwatch/native-rpm-inventory.txt'
        path.unlink(); path.symlink_to('/etc/passwd')
        self.check()

    def test_extra_gui_network_grant(self):
        self.manifest['flatpak']['finishArgs'] = [*self.manifest['flatpak']['finishArgs'], '--share=network']
        self.check()

    def test_changed_artifact_license(self):
        name = 'usr/share/licenses/snitchwatch-bridge/LICENSE'
        self.write(name, 'different license'); self.update_hash(name)
        self.check()

    def test_missing_legacy_user_mask(self):
        (self.root / 'usr/lib/systemd/user/snitchwatch-bridge.service').unlink()
        self.check()

    def test_native_version_mismatch(self):
        name = 'usr/share/snitchwatch/schema1-artifact-MANIFEST.json'
        original = json.loads((self.root / name).read_text())
        original['version'] = '0.1.0'
        self.write(name, json.dumps(original)); self.update_hash(name)
        self.check()

    def test_profile_selectors(self):
        for bridge, firewall, valid in [('legacy', 'opensnitch', True), ('legacy', 'portmaster', True), ('system', 'opensnitch', True), ('system', 'portmaster', False), ('wrong', 'opensnitch', False), ('legacy', 'wrong', False)]:
            with self.subTest(bridge=bridge, firewall=firewall):
                env = {**os.environ, 'SNITCHWATCH_BRIDGE': bridge, 'FIREWALL_DAEMON': firewall}
                result = subprocess.run(['bash', str(FACTORY / 'snitchwatch-system-mode.sh')], env=env, capture_output=True)
                self.assertEqual(result.returncode, 0 if valid else 1)


class StageOutputContracts(unittest.TestCase):
    """Run the real stage.py against fixture inputs (no network, no build)."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='sw-stage-contract.')
        self.addCleanup(self.work.cleanup)
        self.tmp = Path(self.work.name)

    def write(self, path, data, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        path.chmod(mode)
        return path

    def run_stage(self, stage_script, tamper=None):
        source, artifact = self.tmp / 'source', self.tmp / 'artifact'
        upstream_config = b'{"DefaultAction": "deny", "ProcMonitorMethod": "ebpf"}\n'
        self.write(source / 'packaging/system/opensnitchd-default-config.json', upstream_config)
        # Like upstream stage.sh: stages its own (ebpf) daemon config into the root.
        self.write(source / 'packaging/system/stage.sh', 'set -eu\n' + stage_script, 0o755)
        self.write(source / 'packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml', 'app-id: org.snitchwatch.Snitchwatch\n')
        (source / 'vendor/opensnitch').mkdir(parents=True)
        pins = {'sourceCommit': 'a' * 40, 'sourceTree': 'b' * 40, 'submoduleCommit': 'c' * 40, 'bridgeVersion': '0.1.1',
                'builderImage': 'fixture', 'flatpakRuntimeRef': 'fixture', 'flatpakFinishArgs': [],
                'sourceFiles': {name: sha((source / name).read_bytes()) for name in (
                    'packaging/system/opensnitchd-default-config.json', 'packaging/system/stage.sh',
                    'packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml')}}
        real_pins = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
        pins['rustVersion'] = real_pins['rustVersion']
        for name in ('Cargo.toml', 'Cargo.lock'):
            pins['sourceFiles'][name] = real_pins['sourceFiles'][name]
        self.write(source / 'packaging/scanner/stage.sh', (FIXTURES / 'scanner-stage.sh').read_bytes(), 0o755)
        for name in ('org.snitchwatch.Scanner.service', 'snitchwatch-scanner.conf'):
            self.write(source / 'packaging/scanner' / name, (FIXTURES / name).read_bytes())
        self.write(source / 'packaging/polkit/org.snitchwatch.scanner.policy', (FIXTURES / 'org.snitchwatch.scanner.policy').read_bytes())
        for name in EVENTS_FILES:
            self.write(source / 'packaging/endpoint-events' / name, (FIXTURES / name).read_bytes())
            pins['sourceFiles']['packaging/endpoint-events/' + name] = sha((FIXTURES / name).read_bytes())
        # Synthetic source hash inventory includes files actually present here.
        for name in ('Cargo.toml', 'Cargo.lock'):
            self.write(source / name, name)
            pins['sourceFiles'][name] = sha((source / name).read_bytes())
        if tamper:
            self.write(source / tamper, b'tampered\n')
        pins_path = self.write(self.tmp / 'pins.json', json.dumps(pins))
        # PATH shim: an exact, clean checkout at the pinned commits.
        self.write(self.tmp / 'bin/git', f'''#!/bin/sh
case "$*" in
  *"rev-parse HEAD^{{tree}}") echo {pins['sourceTree']} ;;
  *"/vendor/opensnitch rev-parse HEAD") echo {pins['submoduleCommit']} ;;
  *"rev-parse HEAD") echo {pins['sourceCommit']} ;;
  *"status --porcelain --ignored") ;;
  *) exit 99 ;;
esac
''', 0o755)
        bridge = artifact / 'usr/share/snitchwatch-bridge'
        self.write(bridge / 'MANIFEST.json', json.dumps({'version': '0.1.1', 'source': {'git_commit': pins['sourceCommit'], 'dirty': False}}))
        self.write(bridge / 'SHA256SUMS', 'fixture\n')
        self.write(artifact / 'usr/bin/snitchwatch-bridge-cli', '#!/bin/sh\necho snitchwatch-bridge-cli 0.1.1\n', 0o755)
        for name in ('LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md'):
            self.write(artifact / 'usr/share/licenses/snitchwatch-bridge' / name, 'license fixture\n')
        verifier = self.write(self.tmp / 'verifier.py', 'REQUIRED = set()\ndef verify(root, image_tree=False):\n    assert image_tree\n    pass\n')
        logs = [self.write(self.tmp / name, name) for name in ('build.log', 'repro.log', 'rpm-inventory.txt', 'artifact.tar.gz')]
        scanner = self.tmp / 'scanner'
        scanner_fixture(scanner, pins, sha(logs[2].read_bytes()))
        out = self.tmp / 'out'
        argv = ['python3', str(FACTORY / 'snitchwatch-system-stage.py'), '--source', str(source), '--artifact', str(artifact),
                '--out', str(out), '--pins', str(pins_path), '--verifier', str(verifier),
                '--scanner-artifact', str(scanner),
                '--legacy-config', str(ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-default-config.json'),
                '--candidate-config', str(ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'),
                '--build-log', str(logs[0]), '--repro-log', str(logs[1]), '--rpm-inventory', str(logs[2]),
                '--tarball', str(logs[3]), '--os-update-rule', str(OS_UPDATE_SOURCE), '--resolved-dns-rule', str(DNS_SOURCE)]
        env = {**os.environ, 'PATH': str(self.tmp / 'bin') + os.pathsep + os.environ['PATH']}
        return subprocess.run(argv, env=env, capture_output=True, text=True), out

    def test_upstream_ebpf_daemon_config_is_not_in_stage_output(self):
        # Review L-3: upstream's ebpf default-config.json must never reach the
        # image overlay; 95-firewall.sh installs the reviewed deny+proc config.
        result, out = self.run_stage('mkdir -p "$1/etc/opensnitchd/rules"\n'
                                     'cp "$(dirname "$0")/opensnitchd-default-config.json" "$1/etc/opensnitchd/default-config.json"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((out / 'etc/opensnitchd/default-config.json').exists())
        self.assertFalse((out / 'etc/opensnitchd/default-config.json').is_symlink())
        self.assertEqual([p.name for p in (out / 'etc/opensnitchd').iterdir()], ['rules'])
        staged_rule = out / OS_UPDATE_RULE
        self.assertEqual(staged_rule.read_bytes(), OS_UPDATE_SOURCE.read_bytes())
        self.assertEqual(staged_rule.stat().st_mode & 0o7777, 0o644)
        self.assertEqual((out / DNS_RULE).read_bytes(), DNS_SOURCE.read_bytes())
        self.assertEqual((out / DNS_RULE).stat().st_mode & 0o7777, 0o644)

    def test_endpoint_events_files_are_staged_inert_from_pinned_source(self):
        result, out = self.run_stage('mkdir -p "$1/etc/opensnitchd"\ncp "$(dirname "$0")/opensnitchd-default-config.json" "$1/etc/opensnitchd/default-config.json"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in (out / EVENTS_DIR).iterdir()), sorted(EVENTS_FILES))
        for name in EVENTS_FILES:
            staged = out / EVENTS_DIR / name
            self.assertEqual(staged.read_bytes(), (FIXTURES / name).read_bytes())
            self.assertEqual(staged.stat().st_mode & 0o7777, 0o644)
        # Nothing is in a unit search path, enabled, or a logger snippet.
        self.assertEqual([p for p in out.rglob('*') if 'endpoint-events' in p.name and EVENTS_DIR not in p.as_posix() + '/'], [])
        self.assertEqual([p for p in out.rglob('*') if 'logger' in p.name.lower()], [])
        self.assertEqual([p for p in out.rglob('*.wants')], [])

    def test_stage_refuses_endpoint_events_file_that_differs_from_pin(self):
        result, _ = self.run_stage('mkdir -p "$1/etc/opensnitchd"\ncp "$(dirname "$0")/opensnitchd-default-config.json" "$1/etc/opensnitchd/default-config.json"\n',
                                   tamper='packaging/endpoint-events/snitchwatch-endpoint-events.nft')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from pin', result.stderr)

    def test_stage_refuses_when_upstream_config_was_not_staged(self):
        result, _ = self.run_stage('mkdir -p "$1/etc/opensnitchd"\nprintf "{}" > "$1/etc/opensnitchd/default-config.json"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not stage the pinned upstream daemon config', result.stderr)


class FlatpakFinishArgsContract(unittest.TestCase):
    """The recorded finish args must be the pinned manifest's, byte for byte in order.

    The tower stages and later compares this list; if it lags the manifest the
    installed GUI's real sandbox permissions are not what the image declares.
    """
    MANIFEST_NAME = 'org.snitchwatch.Snitchwatch.system.yml'

    def setUp(self):
        self.pins = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
        self.manifest = (FIXTURES / self.MANIFEST_NAME).read_bytes()

    def test_fixture_is_the_pinned_manifest(self):
        pinned = self.pins['sourceFiles']['packaging/flatpak/' + self.MANIFEST_NAME]
        self.assertEqual(sha(self.manifest), pinned)

    def test_recorded_finish_args_match_the_pinned_manifest(self):
        block = re.search(r'^finish-args:\n(.*?)^modules:', self.manifest.decode(), re.M | re.S)
        self.assertIsNotNone(block, 'manifest has no finish-args block')
        declared = re.findall(r'^\s+- (--\S+)\s*$', block.group(1), re.M)
        self.assertEqual(self.pins['flatpakFinishArgs'], declared)


if __name__ == '__main__':
    unittest.main()
