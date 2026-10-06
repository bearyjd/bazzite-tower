#!/usr/bin/env python3
"""Behavioral build-selector and immutable system-manifest contracts.

Metadata fixtures deliberately do not execute a binary. Actual compilation,
schema1 verification, staging and reproducibility have separate container gates.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
FIXTURES = Path(__file__).with_name('test-snitchwatch-image-build-fixtures')
MANIFEST = 'usr/share/snitchwatch/system-bridge-manifest.json'
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
                 'snitchwatch.conf.sysusers': 'usr/lib/sysusers.d/'}
        for name, directory in units.items():
            target = 'snitchwatch.conf' if name.endswith('.sysusers') else name
            self.write(directory + target, (FIXTURES / name).read_bytes())
        for name in ['LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md']:
            self.write('usr/share/licenses/snitchwatch-bridge/' + name, 'License metadata fixture\n')
        self.write('usr/share/bazzite-tower/snitchwatch-bridge-profile', 'system\n')
        self.write('usr/share/bazzite-tower/opensnitchd-default-config.json', (ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-default-config.json').read_bytes())
        self.write('usr/share/bazzite-tower/opensnitchd-system-bridge-config.json', (ROOT / 'system_files/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json').read_bytes())
        dropin = '[Unit]\nRequires=snitchwatch-system-bridge-grpc.socket\nAfter=snitchwatch-system-bridge-grpc.socket\n\n[Service]\nWorkingDirectory=/run/snitchwatch\n'
        for name in ['usr/lib/systemd/system/opensnitch.service.d/20-system-bridge.conf', 'usr/share/bazzite-tower/snitchwatch/opensnitch.service.d/20-system-bridge.conf']:
            self.write(name, dropin)
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

    def test_self_consistent_policy_drift(self):
        name = 'usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'
        self.write(name, json.dumps({'DefaultAction': 'deny', 'ProcMonitorMethod': 'proc', 'Server': {'Address': 'unix:opensnitchd.sock'}})); self.update_hash(name)
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
