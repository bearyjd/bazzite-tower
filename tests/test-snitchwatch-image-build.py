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
OS_UPDATE_RULE = 'etc/opensnitchd/rules/001-tower-os-update.json'
OS_UPDATE_SOURCE = FACTORY / 'snitchwatch-system-os-update-rule.json'
DNS_RULE = 'etc/opensnitchd/rules/002-tower-resolved-dns.json'
DNS_SOURCE = FACTORY / 'snitchwatch-system-resolved-dns-rule.json'
sha = lambda data: hashlib.sha256(data).hexdigest()


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
        inventory = 'rust-0:1.98.1-1.fc44.x86_64\n'
        self.write('usr/share/snitchwatch/native-rpm-inventory.txt', inventory)
        artifact = {'schema_version': 1, 'version': self.pins['bridgeVersion'], 'source': {'git_commit': self.pins['sourceCommit'], 'dirty': False},
                    'upstream': {'opensnitch_commit': self.pins['submoduleCommit']},
                    'build': {'builder_image': self.pins['builderImage'], 'rustc_vv': 'rustc ' + self.pins['rustVersion'] + ' (test)', 'protoc_version': 'libprotoc ' + self.pins['protocVersion']},
                    'files': [{'path': str(p.relative_to(self.root)), 'sha256': sha(p.read_bytes())} for p in sorted(self.root.rglob('*')) if p.is_file() and ('licenses' in p.parts or p == self.root / binary)]}
        self.write('usr/share/snitchwatch/schema1-artifact-MANIFEST.json', json.dumps(artifact))
        self.write('usr/share/snitchwatch/schema1-artifact-SHA256SUMS', 'Original artifact checksum metadata fixture\n')
        self.write('usr/share/snitchwatch/native-build-provenance.json', json.dumps({'sourceCommit': self.pins['sourceCommit'], 'submoduleCommit': self.pins['submoduleCommit'], 'sourceTree': self.pins['sourceTree'], 'reproducible': True, 'rpmInventorySha256': sha(inventory.encode())}))
        mask = self.root / 'usr/lib/systemd/user/snitchwatch-bridge.service'
        mask.parent.mkdir(parents=True); mask.symlink_to('/dev/null')
        self.manifest = {'schemaVersion': 1, 'profile': 'system', 'source': {'commit': self.pins['sourceCommit'], 'submoduleCommit': self.pins['submoduleCommit'], 'tree': self.pins['sourceTree']}, 'binary': {'path': '/' + binary, 'sha256': sha((self.root / binary).read_bytes()), 'version': self.pins['bridgeVersion']},
                         'files': {'/' + str(p.relative_to(self.root)): sha(p.read_bytes()) for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()},
                         'symlinks': {'/usr/lib/systemd/user/snitchwatch-bridge.service': '/dev/null'},
                         'flatpak': {'appId': 'org.snitchwatch.Snitchwatch', 'profile': 'system', 'runtimeRef': self.pins['flatpakRuntimeRef'], 'sourceCommit': self.pins['sourceCommit'], 'manifestSha256': self.pins['sourceFiles']['packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'], 'finishArgs': self.pins['flatpakFinishArgs']}}
        self.save()

    def save(self):
        self.write(MANIFEST, json.dumps(self.manifest))

    def check(self, valid=False):
        self.save()
        result = subprocess.run(['python3', str(FACTORY / 'snitchwatch-system-verify.py'), '--root', str(self.root)], capture_output=True, text=True)
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
        # 22 with its companion resolver DNS rule.
        self.assertEqual(json.loads(result.stdout)['verifiedFiles'], 22)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()})

    def test_binary_tamper(self):
        self.write('usr/bin/snitchwatch-bridge-cli', 'tamper', 0o755)
        self.check()

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
                     '/etc/opensnitchd/rules/../rules/001-tower-os-update.json',
                     '/etc/opensnitchd/rules/../rules/002-tower-resolved-dns.json', '/usr/../etc/shadow']:
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
        copy = self.root / 'elsewhere/001-tower-os-update.json'
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
                             ('nevra', 'rust-0:1.99.0-1.fc44.x86_64'),
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
            'rust': ('4f4a346f18eddff7ee512850a65e9a0a6b0f1ccfe96b1e665a99b6d02dacfa76', 31568956),
            'cargo': ('b32e8b630c55c5ae0df6a3efdf58173974d4dc462a2e2be7a10cd18ea365d30f', 8292119),
            'rust-std-static': ('612185594cb772f5320d5c9a97dd226390fb32972389a47a99b3cb07786c39c4', 42066597),
        }
        rpms = self.pins['toolchainRpms']
        self.assertEqual(len(rpms), 3)
        self.assertEqual({r['name'] for r in rpms}, set(expected))
        for rpm in rpms:
            name = rpm['name']
            filename = f'{name}-1.98.1-1.fc44.x86_64.rpm'
            self.assertEqual((rpm['sha256'], rpm['size']), expected[name])
            self.assertEqual(rpm['file'], filename)
            self.assertEqual(rpm['nevra'], f'{name}-0:1.98.1-1.fc44.x86_64')
            self.assertEqual(rpm['kojiPath'], 'rust/1.98.1/1.fc44/data/signed/6d9f90a6/x86_64/' + filename)
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
    echo 'rustc 1.98.1 (fixture)'
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
        # Without the '-' a missing /run/snitchwatch would keep the fail-closed
        # daemon from starting; the drop-in is pinned to upstream's bytes.
        name = 'usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf'
        self.write(name, (self.root / name).read_text().replace('WorkingDirectory=-/run', 'WorkingDirectory=/run')); self.update_hash(name)
        self.assertIn('system overlay differs from pinned source: /' + name, self.check().stderr)

    def test_self_consistent_policy_drift(self):
        name = 'usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'
        self.write(name, json.dumps({'DefaultAction': 'allow', 'ProcMonitorMethod': 'proc', 'Server': {'Address': 'unix:opensnitchd.sock'}})); self.update_hash(name)
        self.check()

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

    def run_stage(self, stage_script):
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
        verifier = self.write(self.tmp / 'verifier.py', 'REQUIRED = set()\ndef verify(root):\n    pass\n')
        logs = [self.write(self.tmp / name, name) for name in ('build.log', 'repro.log', 'rpm-inventory.txt', 'artifact.tar.gz')]
        out = self.tmp / 'out'
        argv = ['python3', str(FACTORY / 'snitchwatch-system-stage.py'), '--source', str(source), '--artifact', str(artifact),
                '--out', str(out), '--pins', str(pins_path), '--verifier', str(verifier),
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

    def test_stage_refuses_when_upstream_config_was_not_staged(self):
        result, _ = self.run_stage('mkdir -p "$1/etc/opensnitchd"\nprintf "{}" > "$1/etc/opensnitchd/default-config.json"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not stage the pinned upstream daemon config', result.stderr)


if __name__ == '__main__':
    unittest.main()
