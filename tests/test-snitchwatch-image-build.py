#!/usr/bin/env python3
"""Behavioral build-selector and immutable system-manifest contracts.

Metadata fixtures deliberately do not execute a binary. Actual compilation,
schema1 verification, staging and reproducibility have separate container gates.
"""
import hashlib
import json
import os
from pathlib import Path
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
        # is gone; upstream's pinned drop-in is the one daemon drop-in.
        self.assertEqual(json.loads(result.stdout)['verifiedFiles'], 20)
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

    def test_read_file_allows_only_the_fetch_rule_under_etc(self):
        module = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        self.write('etc/opensnitchd/rules/other.json', '{}')
        self.write('etc/shadow', 'x')
        for name in ['/etc/shadow', '/etc/opensnitchd/rules/other.json', '/etc/opensnitchd/rules/../rules/000-snitchwatch-bridge-fetch.json', '/usr/../etc/shadow']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'invalid immutable file path'):
                module['read_file'](self.root, name)
        self.assertEqual(module['read_file'](self.root, '/' + FETCH_RULE), (self.root / FETCH_RULE).read_bytes())

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
        self.assertIn('symlink in immutable file path: /' + FETCH_RULE, result.stderr)

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


if __name__ == '__main__':
    unittest.main()
