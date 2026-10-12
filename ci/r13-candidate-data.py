#!/usr/bin/env python3
"""Collect installed image identity and OCI data in the credential-free builder."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('publication', Path(__file__).with_name('r13-publication.py'))
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    pub.require(not args.out.exists(), 'output must be new')
    ids = pub.candidate_approval(args.control, os.environ['GITHUB_SHA'])
    args.out.mkdir(parents=True)
    run = lambda *values: subprocess.run(values, check=True)
    image = 'localhost/bazzite-tower:r13-build'
    # bootc OS images can omit CMD/ENTRYPOINT. This command only satisfies
    # creation; the extraction container is never started.
    cid = subprocess.check_output(['podman', 'create', image, '/usr/bin/true'], text=True).strip()
    try:
        for kind in ('bridge', 'daemon'):
            run('podman', 'cp', cid + ':/usr/share/snitchwatch/system-' + kind + '-manifest.json', str(args.out / (kind + '-manifest.json')))
            manifest = pub.decode(pub.read(args.out / (kind + '-manifest.json')))
            with tempfile.TemporaryDirectory() as tmp:
                binary = Path(tmp) / 'binary'
                run('podman', 'cp', cid + ':' + manifest['binary']['path'], str(binary))
                pub.require(pub.file_sha(binary) == manifest['binary']['sha256'], 'installed binary differs from manifest')
        with (args.out / 'rpms.tsv').open('w') as inventory:
            subprocess.run(['podman', 'run', '--rm', '--entrypoint', '/usr/bin/rpm', image,
                            '-qa', '--qf', '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'], stdout=inventory, check=True)
        created = subprocess.check_output(['git', '-C', str(args.source), 'log', '-1', '--format=%cI'], text=True).strip()
        run('python3', str(args.source / 'ci/rpm-inventory-spdx.py'), '--input', str(args.out / 'rpms.tsv'),
            '--output', str(args.out / 'sbom.json'), '--created', created)
        (args.out / 'rpms.tsv').unlink()
        # A conversion that changes the manifest is a hard HOLD.
        # Use Podman's existing rootless namespace and the same user's image store.
        run('podman', 'unshare', 'skopeo', 'copy', '--all', '--preserve-digests', 'containers-storage:' + image,
            'oci-archive:' + str(args.out / 'image.oci.tar') + ':r13-build')
        installed = pub.image_identity(args.out / 'image.oci.tar')
        for name, source in [('cosign.pub', 'cosign.pub'), ('policy.json', 'system_files/usr/share/bazzite-tower/containers/policy.json'),
                             ('bazzite-tower.yaml', 'system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml')]:
            shutil.copyfile(args.control / source, args.out / name)
        provenance = {'schemaVersion': 1, 'kind': 'candidate-build', 'repository': pub.REPOSITORY,
                      'workflow': pub.CANDIDATE_WORKFLOW, 'ref': pub.CANDIDATE_REF, 'runId': int(os.environ['GITHUB_RUN_ID']),
                      'runAttempt': 1, 'identities': ids, 'image': installed, 'sbomSha256': pub.file_sha(args.out / 'sbom.json'),
                      'bridgeManifestSha256': pub.file_sha(args.out / 'bridge-manifest.json'),
                      'daemonManifestSha256': pub.file_sha(args.out / 'daemon-manifest.json')}
        (args.out / 'build-provenance.json').write_bytes(pub.canonical(provenance))
        candidate = {'schemaVersion': 1, 'repository': pub.CANDIDATE_REPOSITORY, 'ref': pub.CANDIDATE_REF,
                     'workflow': pub.CANDIDATE_WORKFLOW, 'runId': int(os.environ['GITHUB_RUN_ID']), 'runAttempt': 1,
                     'identities': ids, 'image': installed, 'files': {p.name: pub.file_sha(p) for p in args.out.iterdir()}}
        (args.out / 'candidate.json').write_bytes(pub.canonical(candidate))
        pub.validate_candidate(args.control, args.out, os.environ['GITHUB_SHA'], int(os.environ['GITHUB_RUN_ID']))
    finally:
        run('podman', 'rm', cid)


if __name__ == '__main__':
    main()
