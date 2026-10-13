#!/usr/bin/env python3
"""Read-only r13 publication gates. Trust only primary-reviewed control Git data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

REPOSITORY = 'bearyjd/bazzite-tower'
CANDIDATE_REPOSITORY = 'ghcr.io/bearyjd/bazzite-tower-r13-candidate'
STABLE_REPOSITORY = 'ghcr.io/bearyjd/bazzite-tower'
CANDIDATE_REF = 'refs/heads/ci/r13-candidate-hold-promotion'
CANDIDATE_WORKFLOW = '.github/workflows/r13-candidate.yml'
FILES = {'image.oci.tar', 'candidate.json', 'sbom.json', 'build-provenance.json',
         'bridge-manifest.json', 'daemon-manifest.json', 'cosign.pub', 'policy.json', 'bazzite-tower.yaml'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def decode(data):
    require(len(data) <= 16 * 1024 * 1024, 'oversized JSON')
    return json.loads(data, object_pairs_hook=pairs)


def hex_value(value, size=64):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value), 'invalid hash')
    return value


def digest(value):
    require(isinstance(value, str) and value.startswith('sha256:'), 'invalid image digest')
    hex_value(value[7:])
    return value


def read(path, limit=16 * 1024 * 1024):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'invalid data file: ' + str(path))
    return path.read_bytes()


def file_sha(path):
    require(not path.is_symlink() and path.is_file(), 'invalid artifact file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashed_json(root, category, value):
    hex_value(value)
    data = read(root / '.github/r13' / category / (value + '.json'))
    require(sha(data) == value, 'trusted receipt/packet hash mismatch')
    result = decode(data)
    require(data == canonical(result), 'noncanonical trusted packet/receipt')
    return result


def receipt(root, value, kind, packet_hash, identities):
    r = hashed_json(root, 'receipts', value)
    require(set(r) == {'schemaVersion', 'kind', 'packetSha256', 'identities', 'reviewer', 'evidenceSha256', 'verdict'}, 'invalid receipt fields')
    require(r['schemaVersion'] == 1 and r['kind'] == kind and r['verdict'] == 'PASS', 'receipt refused')
    require(r['packetSha256'] == packet_hash and r['identities'] == identities, 'stale or substituted receipt')
    require(isinstance(r['reviewer'], str) and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}', r['reviewer']), 'missing trusted reviewer')
    hex_value(r['evidenceSha256'])
    return r


def authority(root):
    value = decode(read(root / '.github/r13/authority.json'))
    require(set(value) == {'schemaVersion', 'candidate', 'accepted'}, 'invalid authority fields')
    require(value['schemaVersion'] == 1, 'invalid authority version')
    return value


def identity(value):
    require(set(value) == {'towerCommit', 'towerTree', 'sourceCommit', 'sourceTree', 'submoduleCommit',
                           'bridgePinsSha256', 'daemonPinsSha256', 'workflowSha256'}, 'invalid source identity fields')
    for key in ('towerCommit', 'towerTree', 'sourceCommit', 'sourceTree', 'submoduleCommit'):
        hex_value(value[key], 40)
    for key in ('bridgePinsSha256', 'daemonPinsSha256', 'workflowSha256'):
        hex_value(value[key])


def candidate_approval(root, head):
    hex_value(head, 40)
    approved = authority(root)['candidate']
    require(isinstance(approved, dict) and set(approved) == {'identities', 'reviewReceiptSha256'}, 'candidate HOLD: no reviewed authority')
    ids = approved['identities']
    identity(ids)
    require(ids['towerCommit'] == head, 'unreviewed candidate head')
    receipt(root, approved['reviewReceiptSha256'], 'independent-workflow-review', sha(canonical(ids)), ids)
    return ids


def context(head, ref, workflow, control_head):
    hex_value(control_head, 40)
    hex_value(head, 40)
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY, 'wrong repository')
    require(os.environ.get('GITHUB_EVENT_NAME') in {'push', 'workflow_dispatch'}, 'reviewed candidate push/manual dispatch required')
    require(os.environ.get('GITHUB_SHA') == head, 'unreviewed workflow head')
    require(ref == CANDIDATE_REF and workflow == REPOSITORY + '/' + CANDIDATE_WORKFLOW + '@' + CANDIDATE_REF, 'wrong workflow/ref')
    require(os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'rerun needs fresh review/identity')


def verify_source(root, ids):
    git = lambda *args: subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(git('rev-parse', 'HEAD') == ids['towerCommit'] and git('rev-parse', 'HEAD^{tree}') == ids['towerTree'], 'tower source mismatch')
    require(not git('status', '--porcelain', '--untracked-files=all'), 'dirty tower source')
    require(file_sha(root / CANDIDATE_WORKFLOW) == ids['workflowSha256'], 'workflow bytes changed')
    factory = root / 'build_files/firewall'
    pins = decode(read(factory / 'snitchwatch-system-pins.json'))
    for key in ('sourceCommit', 'sourceTree', 'submoduleCommit'):
        require(pins[key] == ids[key], 'source pin mismatch')
    for kind in ('bridge', 'daemon'):
        name = 'snitchwatch-system-' + ('daemon-' if kind == 'daemon' else '') + 'pins.json'
        require(file_sha(factory / name) == ids[kind + 'PinsSha256'], 'pins hash mismatch')


def image_identity(archive):
    """Inspect OCI headers/blobs as data; never unpack a rootfs or follow links."""
    with tarfile.open(archive, 'r:') as tar:
        members = {}
        for member in tar:
            name = member.name.removeprefix('./')
            if name in {'', '.'}:
                require(member.isdir(), 'unsafe OCI root entry')
                continue
            require(name not in members and not name.startswith('/') and '..' not in Path(name).parts, 'unsafe/duplicate OCI entry')
            require(member.isdir() or member.isfile(), 'OCI link/special entry')
            require(name in {'index.json', 'oci-layout', 'blobs', 'blobs/sha256'} or re.fullmatch('blobs/sha256/[0-9a-f]{64}', name), 'unexpected OCI entry')
            members[name] = member
            require(len(members) <= 10000, 'oversized OCI inventory')

        def blob(name, expected=None):
            require(name in members and members[name].isfile() and members[name].size <= 16 * 1024 * 1024, 'invalid OCI metadata')
            data = tar.extractfile(members[name]).read()
            if expected:
                require('sha256:' + sha(data) == expected, 'OCI metadata hash mismatch')
            return decode(data)

        require(blob('oci-layout') == {'imageLayoutVersion': '1.0.0'}, 'invalid OCI layout')
        index = blob('index.json')
        require(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1, 'one immutable image required')
        descriptor = index['manifests'][0]
        selected = digest(descriptor['digest'])
        require(members['blobs/sha256/' + selected[7:]].size == descriptor['size'], 'OCI descriptor size mismatch')
        manifest = blob('blobs/sha256/' + selected[7:], selected)
        # This one-machine image is deliberately single-platform. Refuse an
        # index instead of silently collapsing it during transport.
        require(manifest.get('mediaType') in {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}, 'multi-platform index HOLD')
        config_digest = digest(manifest['config']['digest'])
        config = blob('blobs/sha256/' + config_digest[7:], config_digest)
        require(config.get('architecture') == 'amd64' and config.get('os') == 'linux', 'wrong platform')
        for item in [manifest['config']] + manifest['layers']:
            value = digest(item['digest'])
            name = 'blobs/sha256/' + value[7:]
            require(name in members and members[name].isfile() and members[name].size == item['size'], 'missing OCI blob')
            stream = tar.extractfile(members[name])
            require('sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest() == value, 'OCI blob hash mismatch')
        return {'manifestDigest': selected, 'indexDigest': None, 'platformDigest': selected, 'platform': 'linux/amd64'}


def validate_candidate(root, data, head, run_id):
    ids = candidate_approval(root, head)
    require(set(p.name for p in data.iterdir()) == FILES, 'unexpected artifact inventory')
    for path in data.iterdir():
        limit = 40 * 1024 ** 3 if path.name == 'image.oci.tar' else 16 * 1024 ** 2
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'invalid/oversized artifact')
    candidate = decode(read(data / 'candidate.json'))
    require(set(candidate) == {'schemaVersion', 'repository', 'ref', 'workflow', 'runId', 'runAttempt', 'identities', 'image', 'files'}, 'invalid candidate metadata')
    require(candidate['schemaVersion'] == 1 and candidate['repository'] == CANDIDATE_REPOSITORY and candidate['ref'] == CANDIDATE_REF and candidate['workflow'] == CANDIDATE_WORKFLOW, 'candidate target/ref substitution')
    require(candidate['runId'] == run_id and candidate['runAttempt'] == 1 and candidate['identities'] == ids, 'candidate CI/source identity mismatch')
    require(candidate['image'] == image_identity(data / 'image.oci.tar'), 'candidate manifest/index/platform substitution')
    require(set(candidate['files']) == FILES - {'candidate.json'}, 'invalid candidate file hashes')
    for name, expected in candidate['files'].items():
        hex_value(expected)
        require(file_sha(data / name) == expected, 'artifact hash mismatch: ' + name)
    for name, source in [('cosign.pub', 'cosign.pub'), ('policy.json', 'system_files/usr/share/bazzite-tower/containers/policy.json'),
                         ('bazzite-tower.yaml', 'system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml')]:
        require(file_sha(data / name) == file_sha(root / source), 'trust policy/key substitution')
    bridge = decode(read(data / 'bridge-manifest.json'))
    daemon = decode(read(data / 'daemon-manifest.json'))
    require(bridge['profile'] == daemon['profile'] == 'system', 'wrong installed profile')
    require(bridge['source'] == {'commit': ids['sourceCommit'], 'tree': ids['sourceTree'], 'submoduleCommit': ids['submoduleCommit']}, 'bridge source mismatch')
    require(bridge['binary']['path'] == '/usr/bin/snitchwatch-bridge-cli' and daemon['binary']['path'] == '/usr/bin/opensnitchd', 'installed binary path substitution')
    require(daemon['source']['commit'] == ids['submoduleCommit'], 'daemon/submodule mismatch')
    hex_value(bridge['binary']['sha256']); hex_value(daemon['binary']['sha256'])
    sbom = decode(read(data / 'sbom.json'))
    require(sbom.get('spdxVersion') == 'SPDX-2.3' and sbom.get('packages'), 'invalid RPM inventory')
    provenance = decode(read(data / 'build-provenance.json'))
    require(provenance == {'schemaVersion': 1, 'kind': 'candidate-build', 'repository': REPOSITORY, 'workflow': CANDIDATE_WORKFLOW,
                           'ref': CANDIDATE_REF, 'runId': run_id, 'runAttempt': 1, 'identities': ids, 'image': candidate['image'],
                           'sbomSha256': candidate['files']['sbom.json'], 'bridgeManifestSha256': candidate['files']['bridge-manifest.json'],
                           'daemonManifestSha256': candidate['files']['daemon-manifest.json']}, 'build provenance substitution')
    return candidate


def output(values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in values.items():
            require(isinstance(key, str) and re.fullmatch('[a-z_][a-z0-9_]*', key), 'invalid output name')
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))
            require('\n' not in text and '\r' not in text, 'output injection')
            stream.write(key + '=' + text + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['approval', 'source', 'candidate'])
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    head = os.environ.get('R13_CANDIDATE_HEAD', '')
    context(head, os.environ.get('GITHUB_REF', ''), os.environ.get('GITHUB_WORKFLOW_REF', ''), os.environ.get('R13_CONTROL_HEAD', ''))
    if args.mode == 'approval':
        candidate_approval(args.control, head)
    elif args.mode == 'source':
        verify_source(args.source, candidate_approval(args.control, head))
    else:
        c = validate_candidate(args.control, args.data, head, int(os.environ['GITHUB_RUN_ID']))
        output({'digest': c['image']['manifestDigest'], 'hashes': {p.name: file_sha(p) for p in args.data.iterdir()},
                'head': head, 'run_id': os.environ['GITHUB_RUN_ID']})


if __name__ == '__main__':
    main()
