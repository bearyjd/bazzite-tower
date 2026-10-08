#!/usr/bin/env python3
"""Daemon metadata tamper gates; actual compile/repro/ABI proof is separate.

Each fixture gets an explicit synthetic trust anchor and nonexecuted ELF header.
Production pin/patch bytes are checked independently below without an override.
"""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
sha = lambda data: hashlib.sha256(data).hexdigest()


class DaemonBuildContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sw-daemon-contract.')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.module = runpy.run_path(str(FACTORY / 'snitchwatch-system-daemon-verify.py'))
        self.verify = self.module['verify']
        self.pins = json.loads((FACTORY / 'snitchwatch-system-daemon-pins.json').read_text())
        base = self.module['ROOT']
        self.pins['patchSha256'] = sha(b'reviewed synthetic patch\n')
        self.pins['protoSourceSha256'] = sha(b'synthetic proto\n')
        self.pins['licenseSha256'] = sha(b'synthetic GPL license\n')
        self.pins['moduleLocks'] = {'daemon/go.mod': sha(b'locked synthetic module\n'), 'daemon/go.sum': sha(b'locked synthetic sums\n')}
        self.pins['generatedFiles'] = {name: sha(('generated ' + name).encode()) for name in self.pins['generatedFiles']}
        self.write(self.module['PINS'], json.dumps(self.pins))
        self.verify.__globals__['EXPECTED_PINS_SHA256'] = sha(self.path(self.module['PINS']).read_bytes())
        self.write(self.module['PATCH'], b'reviewed synthetic patch\n')
        self.write(self.module['CANDIDATE'], b'\x7fELF\x02\x01' + bytes(58), 0o755)
        self.write(self.module['BINARY'], self.path(self.module['CANDIDATE']).read_bytes(), 0o755)
        self.write(self.module['VERIFIER'], 'readonly metadata fixture\n', 0o755)
        self.write(base + 'rpm-inventory.txt', 'golang-0:1.26.8-2.fc44.x86_64\n')
        self.write(base + 'source/ui.proto', 'synthetic proto\n')
        self.write(base + 'source/go.mod', 'locked synthetic module\n')
        self.write(base + 'source/go.sum', 'locked synthetic sums\n')
        for name in self.pins['generatedFiles']:
            self.write(base + 'generated/' + name, 'generated ' + name)
        self.write('/usr/share/licenses/snitchwatch-opensnitchd/LICENSE', 'synthetic GPL license\n')
        self.write('/usr/share/licenses/snitchwatch-opensnitchd/THIRD-PARTY-LICENSES.md', '# Linked Go module licenses\n\nModule checksum: `h1:fixture`\n')
        self.write(base + 'go-build-info.txt', 'fixture: go1.26.8-X:nodwarf5\n\tbuild\t-trimpath=true\n\tbuild\tvcs.revision=' + self.pins['sourceCommit'] + '\n\tbuild\tvcs.modified=true\n')
        binary_sha = sha(self.path(self.module['CANDIDATE']).read_bytes())
        self.provenance = {'sourceCommit': self.pins['sourceCommit'], 'sourceTree': self.pins['sourceTree'], 'patchSha256': self.pins['patchSha256'],
                           'patchedFiles': self.pins['patchedFiles'], 'builderImage': self.pins['builderImage'], 'goVersion': self.pins['goVersion'],
                           'buildCommand': self.pins['buildCommand'], 'reproducible': True, 'firstBinarySha256': binary_sha, 'secondBinarySha256': binary_sha,
                           'rpmInventorySha256': sha(self.path(base + 'rpm-inventory.txt').read_bytes()), 'generatedFiles': self.pins['generatedFiles'],
                           'tools': self.pins['tools'], 'actualVersionOutput': self.pins['daemonVersion'],
                           'linkedModules': [{'sum': 'h1:fixture', 'licenses': {'LICENSE': sha(b'license')}}]}
        self.write(base + 'native-build-provenance.json', json.dumps(self.provenance))
        self.manifest = {'schemaVersion': 1, 'profile': 'system', 'source': {'commit': self.pins['sourceCommit'], 'tree': self.pins['sourceTree']},
                         'patch': {'path': self.module['PATCH'], 'sha256': self.pins['patchSha256']},
                         'binary': {'path': self.module['BINARY'], 'candidatePath': self.module['CANDIDATE'], 'sha256': binary_sha, 'version': self.pins['daemonVersion']},
                         'files': {name: sha(self.path(name).read_bytes()) for name in self.module['REQUIRED']}}
        self.save()

    def path(self, name):
        return self.root / name.lstrip('/')

    def write(self, name, data, mode=0o644):
        p = self.path(name); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode()); p.chmod(mode)

    def save(self):
        self.write(self.module['MANIFEST'], json.dumps(self.manifest))

    def change(self, name, data):
        self.write(name, data)
        self.manifest['files'][name] = sha(self.path(name).read_bytes())
        self.save()

    def reject(self, stage_only=False):
        self.save()
        with self.assertRaises((ValueError, OSError)):
            self.verify(self.root, stage_only=stage_only)

    def test_production_pin_and_reviewed_patch_bytes(self):
        real = runpy.run_path(str(FACTORY / 'snitchwatch-system-daemon-verify.py'))
        pins_bytes = (FACTORY / 'snitchwatch-system-daemon-pins.json').read_bytes()
        self.assertEqual(sha(pins_bytes), real['EXPECTED_PINS_SHA256'])
        pins = json.loads(pins_bytes)
        self.assertEqual(sha((FACTORY / 'snitchwatch-system-daemon-shutdown.patch').read_bytes()), pins['patchSha256'])
        self.assertEqual(len(pins['patchedFiles']), 32)

    def test_valid_complete_readonly_default_check(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.verify(self.root)
        self.assertTrue(result['installedBinaryChecked']); self.assertEqual(result['verifiedFiles'], 14)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_stage_check_never_hides_default_installed_mismatch(self):
        self.write(self.module['BINARY'], b'different RPM daemon', 0o755)
        self.assertFalse(self.verify(self.root, stage_only=True)['installedBinaryChecked'])
        self.reject()

    def test_stage_bypass_refused_for_production_root(self):
        with self.assertRaisesRegex(ValueError, 'production root'):
            self.verify(Path('/'), stage_only=True)

    def test_installed_binary_symlink(self):
        p = self.path(self.module['BINARY']); p.unlink(); p.symlink_to(self.path(self.module['CANDIDATE']))
        self.reject()

    def test_self_consistent_patch_tamper(self):
        self.change(self.module['PATCH'], b'foreign patch'); self.reject()

    def test_self_consistent_generated_protocol_tamper(self):
        self.change(self.module['ROOT'] + 'generated/ui.pb.go', b'foreign generated protocol'); self.reject()

    def test_self_consistent_module_lock_tamper(self):
        self.change(self.module['ROOT'] + 'source/go.mod', b'new module'); self.reject()

    def test_self_consistent_license_tamper(self):
        self.change('/usr/share/licenses/snitchwatch-opensnitchd/LICENSE', b'wrong license'); self.reject()

    def test_reproduction_mismatch(self):
        self.provenance['secondBinarySha256'] = '0' * 64
        self.change(self.module['ROOT'] + 'native-build-provenance.json', json.dumps(self.provenance)); self.reject()

    def test_patch_source_inventory_omission(self):
        self.provenance['patchedFiles'] = {}
        self.change(self.module['ROOT'] + 'native-build-provenance.json', json.dumps(self.provenance)); self.reject()

    def test_self_consistent_source_pin_forgery(self):
        self.pins['sourceCommit'] = '0' * 40
        self.change(self.module['PINS'], json.dumps(self.pins)); self.manifest['source']['commit'] = '0' * 40; self.reject()

    def test_build_info_wrong_upstream(self):
        self.change(self.module['ROOT'] + 'go-build-info.txt', 'fixture: go1.26.8-X:nodwarf5\n\tbuild\t-trimpath=true\n\tbuild\tvcs.revision=' + '0' * 40 + '\n\tbuild\tvcs.modified=true\n'); self.reject()

    def test_build_info_hides_downstream_patch(self):
        name = self.module['ROOT'] + 'go-build-info.txt'
        self.change(name, self.path(name).read_text().replace('vcs.modified=true', 'vcs.modified=false')); self.reject()

    def test_inventory_escape(self):
        self.manifest['files']['/etc/shadow'] = '0' * 64; self.reject()

    def test_world_writable_candidate(self):
        self.path(self.module['CANDIDATE']).chmod(0o777); self.reject()


class CompleteSourceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sw-daemon-source.')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        self.patch = self.root / 'reviewed.patch'; self.patch.write_bytes(b'synthetic reviewed patch')
        self.check = runpy.run_path(str(FACTORY / 'snitchwatch-system-daemon-stage.py'))['check_source']
        for name, data in {'LICENSE': 'license', 'proto/ui.proto': 'proto', 'daemon/go.mod': 'module',
                           'daemon/go.sum': 'sum', 'daemon/repaired.go': 'upstream',
                           'daemon/unrelated.go': 'unchanged', '.gitignore': 'daemon/ui/protocol/\ndaemon/ignored/\n'}.items():
            self.write(name, data)
        self.git('init', '-q'); self.git('add', '.')
        self.git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline')
        self.pins = {'sourceCommit': self.git('rev-parse', 'HEAD'), 'sourceTree': self.git('rev-parse', 'HEAD^{tree}'),
                     'patchSha256': sha(self.patch.read_bytes()), 'moduleLocks': {'daemon/go.mod': sha(b'module'), 'daemon/go.sum': sha(b'sum')},
                     'protoSourceSha256': sha(b'proto'), 'licenseSha256': sha(b'license'),
                     'patchedFiles': {'daemon/repaired.go': sha(b'repaired')}, 'generatedFiles': {'ui.pb.go': sha(b'generated')}}

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True).strip()

    def write(self, name, data):
        path = self.source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)

    def reviewed(self):
        self.write('daemon/repaired.go', 'repaired'); self.write('daemon/ui/protocol/ui.pb.go', 'generated')

    def reject(self):
        with self.assertRaisesRegex(ValueError, 'not exactly the reviewed'):
            self.check(self.source, self.pins, self.patch, True)

    def test_clean_baseline_and_exact_reviewed_generated_inventory(self):
        self.check(self.source, self.pins, self.patch, False)
        self.reviewed(); self.check(self.source, self.pins, self.patch, True)

    def test_unrelated_tracked_edit(self):
        self.reviewed(); self.write('daemon/unrelated.go', 'foreign code'); self.reject()

    def test_unrelated_tracked_deletion(self):
        self.reviewed(); (self.source / 'daemon/unrelated.go').unlink(); self.reject()

    def test_extra_untracked_compiler_input(self):
        self.reviewed(); self.write('daemon/extra.go', 'foreign code'); self.reject()

    def test_extra_ignored_compiler_input(self):
        self.reviewed(); self.write('daemon/ignored/extra.go', 'foreign code'); self.reject()

    def test_unrelated_staged_edit(self):
        self.reviewed(); self.write('daemon/unrelated.go', 'foreign code'); self.git('add', 'daemon/unrelated.go'); self.reject()

    def test_baseline_ignored_file_refused(self):
        self.write('daemon/ignored/extra.go', 'foreign code')
        with self.assertRaisesRegex(ValueError, 'not exactly the reviewed'):
            self.check(self.source, self.pins, self.patch, False)


if __name__ == '__main__':
    unittest.main()
