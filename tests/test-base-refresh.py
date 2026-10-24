#!/usr/bin/env python3
"""Negative identity, privilege and downgrade checks for accepted-source refresh."""
import base64
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('refresh', ROOT / 'ci/base-refresh.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.pub.canonical(value))


def archive(path):
    blobs = {}
    def blob(value):
        data = r.pub.canonical(value); digest = 'sha256:' + r.pub.sha(data)
        blobs['blobs/sha256/' + digest[7:]] = data
        return {'digest': digest, 'size': len(data)}
    config = blob({'architecture': 'amd64', 'os': 'linux'})
    layer = blob({'arbitrary': 'fixture data'})
    manifest = blob({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': config, 'layers': [layer]})
    blobs['index.json'] = r.pub.canonical({'schemaVersion': 2, 'manifests': [manifest]})
    blobs['oci-layout'] = r.pub.canonical({'imageLayoutVersion': '1.0.0'})
    with tarfile.open(path, 'w') as tar:
        for name, data in blobs.items():
            info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'control'; self.root.mkdir()
        self.policy = json.loads((ROOT / '.github/base-refresh-policy.json').read_bytes())
        self.path = self.root / '.github/base-refresh-policy.json'; write(self.path, self.policy)
        for path in [*r.TRUST.values(), 'build_files/firewall/snitchwatch-system-pins.json', 'build_files/firewall/snitchwatch-system-daemon-pins.json']:
            dest = self.root / path; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / path, dest)
        self.env = patch.dict(os.environ, {'CONTROL': str(self.root), 'GITHUB_REPOSITORY': 'bearyjd/bazzite-tower',
                      'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': 'a' * 40,
                      'GITHUB_RUN_ID': '17', 'GITHUB_RUN_NUMBER': '1', 'GITHUB_RUN_ATTEMPT': '1',
                      'GITHUB_WORKFLOW_REF': 'bearyjd/bazzite-tower/.github/workflows/base-refresh.yml@refs/heads/main'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.data = Path(self.tmp.name) / 'data'; self.data.mkdir()
        archive(self.data / 'image.oci.tar')
        (self.data / 'rpms.tsv').write_text('kernel-core\t0\t7.2.9\togc3.1.fc44\tx86_64\n')
        records = r.rpm.parse_inventory(self.data / 'rpms.tsv')
        write(self.data / 'sbom.json', r.rpm.make_document(records, self.policy['sourceCreated']))
        self.base = self.policy['upstream'] + '@sha256:' + 'b' * 64
        self.version = '44.20261023'
        write(self.data / 'installed.json', {'profile': 'legacy', 'trust': {name: r.pub.file_sha(self.root / path) for name, path in r.TRUST.items()}})
        self.proof = r.evidence(self.policy, 'legacy', self.base, self.version, r.pub.image_identity(self.data / 'image.oci.tar'), r.pub.file_sha(self.data / 'sbom.json'), '7.2.9-ogc3.1.fc44')
        self.proof['installedSha256'] = r.pub.file_sha(self.data / 'installed.json')
        write(self.data / 'build-provenance.json', self.proof)

    def valid(self):
        return r.validate(self.root, self.data, 'legacy', self.base, self.version)

    def test_valid_artifact_and_stable_alias_identity(self):
        proof, hashes = self.valid()
        self.assertEqual(proof['channels'], ['stable', 'latest'])
        self.assertEqual(proof['sourceCommit'], '91908f11eba496cde806298f2d0cbe332213192d')
        self.assertEqual(set(hashes), r.FILES)

    def test_policy_schema_dest_ref_profile_fail_closed(self):
        mutations = [('ref', 'refs/heads/feature'), ('destination', 'ghcr.io/attacker/image'), ('sourceCommit', 'HEAD'),
                     ('sourceTree', 'f' * 39), ('profiles', {'legacy': {'imageTag': 'latest', 'channels': ['latest']}})]
        for key, value in mutations:
            with self.subTest(key=key):
                p = copy.deepcopy(self.policy); p[key] = value; write(self.path, p)
                with self.assertRaises(ValueError): r.policy(self.root)
        write(self.path, {**self.policy, 'extra': True})
        with self.assertRaises(ValueError): r.policy(self.root)

    def test_context_main_repo_and_event_guards(self):
        r.context()
        for key, value in [('GITHUB_REF', 'refs/heads/feature'), ('GITHUB_REPOSITORY', 'attacker/fork'), ('GITHUB_SHA', 'main'), ('GITHUB_EVENT_NAME', 'push'),
                           ('GITHUB_WORKFLOW_REF', 'bearyjd/bazzite-tower/.github/workflows/other.yml@refs/heads/main'),
                           ('GITHUB_WORKFLOW_REF', 'bearyjd/bazzite-tower/.github/workflows/base-refresh.yml@refs/heads/feature')]:
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError): r.context()

    def test_missing_or_noncanonical_run_identity_refused(self):
        for key in ['GITHUB_RUN_ID', 'GITHUB_RUN_NUMBER', 'GITHUB_RUN_ATTEMPT']:
            for value in ['', '0', '-1', '01', '1.0', '1e0', ' 1', '1\n']:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                    r.context()
            with patch.dict(os.environ):
                del os.environ[key]
                with self.assertRaises(ValueError): r.context()
        with patch.dict(os.environ):
            del os.environ['GITHUB_WORKFLOW_REF']
            with self.assertRaises(ValueError): r.context()

    def test_mutable_and_wrong_repository_base_refused(self):
        for value in ['ghcr.io/ublue-os/bazzite-nvidia-open:stable', 'ghcr.io/evil/base@sha256:' + 'f' * 64]:
            with self.assertRaises(ValueError): r.immutable_base(value, self.policy)

    def test_floor_and_signed_baseline_downgrades_refused(self):
        for kernel, version in [('7.1.99-ogc9.1.fc44', self.version), ('7.2.8-ogc9.1.fc44', self.version), ('7.2.9-ogc2.9.fc44', self.version), ('7.2.9-ogc3.1.fc44', '44.20261022')]:
            with self.subTest(kernel=kernel, version=version), self.assertRaises(ValueError):
                r.evidence(self.policy, 'legacy', self.base, version, self.proof['image'], self.proof['sbomSha256'], kernel)
        previous = copy.deepcopy(self.proof); previous['kernel'] = '7.2.10-ogc1.1.fc44'
        with self.assertRaises(ValueError): r.monotonic(self.policy, self.proof, previous)
        previous = copy.deepcopy(self.proof); previous['baseVersion'] = '44.20261024'
        with self.assertRaises(ValueError): r.monotonic(self.policy, self.proof, previous)
        newer = copy.deepcopy(self.proof); newer['kernel'] = '7.3.0-ogc1.1.fc44'; newer['baseVersion'] = '44.20261024'
        r.monotonic(self.policy, newer, self.proof)

    def test_unsafe_artifact_inventory_and_symlink_refused(self):
        extra = self.data / 'extra'; extra.write_text('bad')
        with self.assertRaises(ValueError): self.valid()
        extra.unlink(); path = self.data / 'rpms.tsv'; saved = path.read_bytes(); path.unlink(); path.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.valid()
        path.unlink(); path.write_bytes(saved)

    def test_sbom_inventory_and_provenance_substitution_refused(self):
        for key, value in [('sourceCommit', 'c' * 40), ('profile', 'system'), ('baseImage', self.base.replace('b' * 64, 'c' * 64)), ('kernel', '7.3.0-ogc1.1.fc44')]:
            proof = copy.deepcopy(self.proof); proof[key] = value; write(self.data / 'build-provenance.json', proof)
            with self.subTest(key=key), self.assertRaises(ValueError): self.valid()
        write(self.data / 'build-provenance.json', self.proof)
        sbom = json.loads((self.data / 'sbom.json').read_bytes()); sbom['packages'][0]['versionInfo'] = '0:99.0.0-fake.x86_64'
        write(self.data / 'sbom.json', sbom)
        with self.assertRaises(ValueError): self.valid()

    def test_blob_tamper_refused(self):
        raw = (self.data / 'image.oci.tar').read_bytes().replace(b'fixture data', b'changed data')
        (self.data / 'image.oci.tar').write_bytes(raw)
        with self.assertRaises(ValueError): self.valid()

    def test_revalidation_requires_exact_fresh_validator_hashes(self):
        proof, hashes = self.valid()
        with patch.dict(os.environ, {'VALIDATED_HASHES': json.dumps(hashes), 'VALIDATED_DIGEST': proof['image']['manifestDigest']}):
            with patch.object(r, 'installed_profile', side_effect=AssertionError('publisher must not execute image')):
                r.revalidate(self.root, self.data, 'legacy', self.base, self.version)
            hashes['sbom.json'] = '0' * 64
            with patch.dict(os.environ, {'VALIDATED_HASHES': json.dumps(hashes)}), self.assertRaises(ValueError):
                r.revalidate(self.root, self.data, 'legacy', self.base, self.version)

    def test_stale_main_policy_refuses_pointer_gate(self):
        current = Path(self.tmp.name) / 'current.json'; current.write_bytes(self.path.read_bytes())
        r.fresh(self.root, current)
        current.write_bytes(current.read_bytes() + b'\n')
        with self.assertRaises(ValueError): r.fresh(self.root, current)

    def test_source_tree_and_dirty_source_refused(self):
        outputs = [self.policy['sourceCommit'], self.policy['sourceTree'], '', self.policy['sourceCreated']]
        with patch.object(r, 'command', side_effect=outputs): r.source(self.root, self.root)
        for outputs in [['c' * 40], [self.policy['sourceCommit'], 'd' * 40], [self.policy['sourceCommit'], self.policy['sourceTree'], '?? injected']]:
            with patch.object(r, 'command', side_effect=outputs), self.assertRaises(ValueError): r.source(self.root, self.root)

    def test_installed_trust_substitution_refused(self):
        installed = json.loads((self.data / 'installed.json').read_bytes())
        installed['trust']['cosign.pub'] = '0' * 64
        write(self.data / 'installed.json', installed)
        with self.assertRaises(ValueError): self.valid()

    def test_baseline_missing_bootstrap_auth_and_network_fail_closed(self):
        from subprocess import CompletedProcess
        def result(error): return CompletedProcess([], 1, b'', error)
        with patch.object(r.subprocess, 'run', return_value=result(b'manifest unknown')):
            r.baseline(self.root, self.data, 'legacy', self.root)
        for error in [b'unauthorized', b'connection reset', b'timeout', b'404 proxy error']:
            with patch.object(r.subprocess, 'run', return_value=result(error)), self.assertRaises(ValueError):
                r.baseline(self.root, self.data, 'legacy', self.root)
        with patch.object(r.subprocess, 'run', return_value=result(b'manifest unknown')), self.assertRaises(ValueError):
            r.baseline(self.root, self.data, 'system', self.root)

    def test_missing_stable_refused_after_first_server_run_or_attempt(self):
        from subprocess import CompletedProcess
        missing = CompletedProcess([], 1, b'', b'manifest unknown')
        for number, attempt in [('2', '1'), ('1', '2'), ('2', '2'), ('01', '1'), ('1', '01'), ('', '1'), ('1', '')]:
            with self.subTest(number=number, attempt=attempt), patch.dict(os.environ, {'GITHUB_RUN_NUMBER': number, 'GITHUB_RUN_ATTEMPT': attempt}), patch.object(r.subprocess, 'run', return_value=missing), self.assertRaises(ValueError):
                r.baseline(self.root, self.data, 'legacy', self.root)
        for key in ['GITHUB_RUN_NUMBER', 'GITHUB_RUN_ATTEMPT']:
            with patch.dict(os.environ), patch.object(r.subprocess, 'run', return_value=missing):
                del os.environ[key]
                with self.assertRaises(ValueError): r.baseline(self.root, self.data, 'legacy', self.root)

    def test_signed_subject_or_predicate_mismatch_refused(self):
        verified = Path(self.tmp.name) / 'verified'; verified.mkdir()
        digest = self.proof['image']['manifestDigest']
        def envelope(kind, predicate, subject=r.DEST):
            return {'payload': base64.b64encode(r.pub.canonical({'predicateType': kind, 'predicate': predicate,
                    'subject': [{'name': subject, 'digest': {'sha256': digest[7:]}}]})).decode()}
        sbom = json.loads((self.data / 'sbom.json').read_bytes())
        write(verified / 'sbom.json', envelope('https://spdx.dev/Document', sbom))
        write(verified / 'provenance.json', envelope(r.PREDICATE, self.proof))
        r.attestations(self.data, verified, digest)
        write(verified / 'provenance.json', envelope(r.PREDICATE, self.proof, subject=r.DEST + ':stable'))
        with self.assertRaises(ValueError): r.attestations(self.data, verified, digest)
        write(verified / 'provenance.json', envelope(r.PREDICATE, {**self.proof, 'profile': 'system'}))
        with self.assertRaises(ValueError): r.attestations(self.data, verified, digest)

    def test_independent_actual_rpm_query_and_native_source_checks(self):
        records = r.rpm.parse_inventory(self.data / 'rpms.tsv')
        installed = json.loads((self.data / 'installed.json').read_bytes())
        with patch.object(r.subprocess, 'run'), patch.object(r, 'command', return_value=self.proof['image']['manifestDigest']), patch.object(r, 'installed_profile', return_value=installed), patch.object(r, 'actual_inventory', return_value=records):
            r.validate(self.root, self.data, 'legacy', self.base, self.version, execute=True)
        with patch.object(r.subprocess, 'run'), patch.object(r, 'command', return_value=self.proof['image']['manifestDigest']), patch.object(r, 'installed_profile', return_value=installed), patch.object(r, 'actual_inventory', return_value=[]), self.assertRaises(ValueError):
            r.validate(self.root, self.data, 'legacy', self.base, self.version, execute=True)
        installed.update(profile='system', bridge={'profile': 'system', 'source': self.policy['nativeSource'], 'binary': {'path': '/usr/bin/snitchwatch-bridge-cli', 'sha256': 'b' * 64}}, daemon={'profile': 'system', 'source': self.policy['daemonSource'], 'binary': {'path': '/usr/bin/opensnitchd', 'sha256': 'd' * 64}, 'patch': {'sha256': self.policy['daemonPatchSha256']}})
        write(self.data / 'installed.json', installed)
        proof = r.evidence(self.policy, 'system', self.base, self.version, self.proof['image'], self.proof['sbomSha256'], self.proof['kernel'])
        proof['installedSha256'] = r.pub.file_sha(self.data / 'installed.json'); write(self.data / 'build-provenance.json', proof)
        r.validate(self.root, self.data, 'system', self.base, self.version)
        installed['daemon']['source'] = {**installed['daemon']['source'], 'commit': '0' * 40}; write(self.data / 'installed.json', installed)
        with self.assertRaises(ValueError): r.validate(self.root, self.data, 'system', self.base, self.version)

    def test_partial_alias_failure_has_recovery_summary(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/base-refresh.yml').read_text())
        step = next(s for s in workflow['jobs']['publish-legacy']['steps'] if s.get('name', '').startswith('Reject signed baseline'))
        pointer = step['run'][step['run'].index('case "$PROFILE"'):]
        summary = Path(self.tmp.name) / 'summary'
        env = {**os.environ, 'PROFILE': 'legacy', 'DEST': r.DEST, 'BASE': self.base,
               'RUNNER_TEMP': self.tmp.name, 'GITHUB_STEP_SUMMARY': str(summary),
               'VALIDATED_DIGEST': 'sha256:' + r.pub.sha(b'{}')}
        mock = """skopeo() {
          if [[ "$1" == inspect ]]; then printf '%s' '{}'; return; fi
          if [[ "${@: -1}" == *:latest ]]; then return 42; fi
          while [[ "$#" -gt 0 ]]; do
            if [[ "$1" == --digestfile ]]; then shift; printf '%s' "$VALIDATED_DIGEST" > "$1"; return; fi
            shift
          done
        }
        """
        result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + mock + pointer], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 42, result.stderr)
        text = summary.read_text()
        self.assertIn('REFRESH_POINTER_FAILURE', text); self.assertIn('attempted=stable latest confirmed=stable', text)
        self.assertIn('expected_digest=' + env['VALIDATED_DIGEST'], text); self.assertIn('Rerun', text)
        self.assertNotIn('REFRESH_PASS', text)

    def test_workflow_permission_split_and_exact_alias_gate(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/base-refresh.yml').read_text()); jobs = workflow['jobs']
        self.assertEqual(workflow['concurrency'], {'group': 'bazzite-tower-stable-writers', 'cancel-in-progress': False})
        self.assertEqual(set(workflow['on']), {'schedule', 'workflow_dispatch'})
        self.assertIn("github.ref == 'refs/heads/main'", jobs['resolve']['if'])
        for name in ['build', 'validate-legacy', 'validate-system']:
            self.assertTrue(all(value == 'read' for value in jobs[name]['permissions'].values()))
            self.assertNotIn('secrets.', json.dumps(jobs[name]))
        for profile in ['legacy', 'system']:
            steps = jobs['publish-' + profile]['steps']; text = '\n'.join(s.get('run', '') for s in steps)
            self.assertFalse(any('actions/checkout@' in s.get('uses', '') for s in steps))
            self.assertIn('contents/$path?ref=$GITHUB_SHA', text)
            self.assertNotIn('podman ', text)
            self.assertIn(' revalidate ', text); self.assertNotIn(' validate ', text)
            self.assertLess(text.index(' baseline '), text.index(' fresh '))
            self.assertLess(text.index(' fresh '), text.index('for tag in'))
            self.assertIn('channels=(stable latest)', text)
            attest = next(s for s in steps if s.get('name') == 'Publish GitHub build provenance for exact image digest')
            self.assertEqual(attest['with']['subject-name'], r.DEST)


if __name__ == '__main__': unittest.main()
