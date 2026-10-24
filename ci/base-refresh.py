#!/usr/bin/env python3
"""Accepted-source base refresh. Trusted control code; image/artifact bytes are data.

Only collect/validate may execute a candidate, in jobs without publication credentials.
The publisher uses revalidate, trust, fresh and attestations and never executes it.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


pub = module('r13-publication')
rpm = module('rpm-inventory-spdx')
require = pub.require
DEST = 'ghcr.io/bearyjd/bazzite-tower'
WORKFLOW = '.github/workflows/base-refresh.yml'
PREDICATE = 'https://github.com/bearyjd/bazzite-tower/attestations/base-refresh/v1'
FILES = {'image.oci.tar', 'rpms.tsv', 'sbom.json', 'build-provenance.json', 'installed.json'}
TRUST = {'cosign.pub': 'cosign.pub', 'policy.json': 'system_files/usr/share/bazzite-tower/containers/policy.json',
         'bazzite-tower.yaml': 'system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml'}


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def policy(root):
    value = pub.decode(pub.read(root / '.github/base-refresh-policy.json'))
    expected = {'schemaVersion', 'repository', 'ref', 'destination', 'upstream', 'upstreamTag', 'minimumBaseVersion',
                'minimumKernel', 'sourceCommit', 'sourceTree', 'sourceCreated', 'profiles', 'bootstrap', 'nativeSource', 'daemonSource', 'daemonPatchSha256'}
    require(set(value) == expected and value['schemaVersion'] == 1, 'invalid refresh policy schema')
    require(value['repository'] == 'bearyjd/bazzite-tower' and value['ref'] == 'refs/heads/main', 'policy repository/ref substitution')
    require(value['destination'] == DEST and value['upstream'] == 'ghcr.io/ublue-os/bazzite-nvidia-open' and value['upstreamTag'] == 'stable', 'policy destination/upstream substitution')
    for key in ('sourceCommit', 'sourceTree'):
        pub.hex_value(value[key], 40)
    require(value['profiles'] == {'legacy': {'imageTag': 'latest', 'channels': ['stable', 'latest']},
                                  'system': {'imageTag': 'snitchwatch-system', 'channels': ['snitchwatch-system']}}, 'policy profile/channel substitution')
    require(value['bootstrap'] == {'legacy': {'allowMissing': True, 'digest': None}, 'system': {'allowMissing': False, 'digest': 'sha256:df4ec1239abae0cbe1d4d4e27bce85d54f17e2b5155e431c2727c724aa879bf7'}}, 'unreviewed bootstrap exception')
    require(set(value['nativeSource']) == {'commit', 'tree', 'submoduleCommit'} and set(value['daemonSource']) == {'commit', 'tree'}, 'invalid native source policy')
    for identity in [*value['nativeSource'].values(), *value['daemonSource'].values()]: pub.hex_value(identity, 40)
    pub.hex_value(value['daemonPatchSha256'])
    base_version(value['minimumBaseVersion']); kernel_version(value['minimumKernel'])
    require(rpm.parse_created(value['sourceCreated']) == value['sourceCreated'], 'invalid source timestamp')
    return value


def context():
    require(os.environ['GITHUB_REPOSITORY'] == 'bearyjd/bazzite-tower' and os.environ['GITHUB_REF'] == 'refs/heads/main', 'main-only refresh')
    require(os.environ['GITHUB_EVENT_NAME'] in {'schedule', 'workflow_dispatch'}, 'unexpected refresh event')
    pub.hex_value(os.environ['GITHUB_SHA'], 40)
    require(int(os.environ['GITHUB_RUN_ID']) > 0 and int(os.environ['GITHUB_RUN_ATTEMPT']) > 0, 'invalid run identity')


def base_version(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{2}\.[0-9]{8}', value), 'invalid Bazzite version')
    return tuple(map(int, value.split('.')))


def kernel_version(value):
    # Deliberately fail closed on a new kernel release naming scheme: it needs
    # review before automatically becoming a hardware baseline.
    match = re.fullmatch(r'([0-9]+)\.([0-9]+)\.([0-9]+)-ogc([0-9]+)\.([0-9]+)\.fc([0-9]+)', value)
    require(match is not None, 'invalid/unreviewed kernel version')
    return tuple(map(int, match.groups()))


def immutable_base(value, p):
    require(value.startswith(p['upstream'] + '@sha256:'), 'mutable/wrong upstream base')
    pub.digest(value.split('@', 1)[1])
    return value


def source(root, source_root):
    p = policy(root)
    git = lambda *args: command('git', '-C', str(source_root), *args)
    require(git('rev-parse', 'HEAD') == p['sourceCommit'] and git('rev-parse', 'HEAD^{tree}') == p['sourceTree'], 'accepted product source/tree mismatch')
    require(not git('status', '--porcelain', '--untracked-files=all'), 'dirty accepted product source')
    require(rpm.parse_created(git('log', '-1', '--format=%cI')) == p['sourceCreated'], 'source timestamp mismatch')
    # Pins, scripts, trust files and allow/proc defaults are all bound by this
    # exact clean tree. No cherry-pick or feature source can enter a refresh.
    for path in TRUST.values():
        require(pub.file_sha(root / path) == pub.file_sha(source_root / path), 'accepted source trust policy/key differs from control')
    pins = pub.decode(pub.read(source_root / 'build_files/firewall/snitchwatch-system-pins.json'))
    daemon = pub.decode(pub.read(source_root / 'build_files/firewall/snitchwatch-system-daemon-pins.json'))
    require({k: pins[v] for k, v in [('commit', 'sourceCommit'), ('tree', 'sourceTree'), ('submoduleCommit', 'submoduleCommit')]} == p['nativeSource'], 'accepted native source pins differ from policy')
    require({k: daemon['source' + k.title()] for k in ['commit', 'tree']} == p['daemonSource'] and daemon['patchSha256'] == p['daemonPatchSha256'], 'accepted daemon source/patch differs from policy')
    return p


def evidence(p, profile, base, version, image, sbom_hash, kernel):
    require(profile in p['profiles'], 'wrong profile')
    immutable_base(base, p)
    require(base_version(version) >= base_version(p['minimumBaseVersion']), 'Bazzite downgrade below accepted floor')
    require(kernel_version(kernel) >= kernel_version(p['minimumKernel']), 'kernel downgrade below accepted floor')
    return {'schemaVersion': 1, 'kind': 'accepted-source-base-refresh', 'repository': p['repository'],
            'workflow': WORKFLOW, 'ref': p['ref'], 'controlCommit': os.environ['GITHUB_SHA'],
            'runId': int(os.environ['GITHUB_RUN_ID']), 'runAttempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
            'policySha256': pub.file_sha(Path(os.environ['CONTROL']) / '.github/base-refresh-policy.json'),
            'sourceCommit': p['sourceCommit'], 'sourceTree': p['sourceTree'], 'profile': profile,
            'imageTag': p['profiles'][profile]['imageTag'], 'channels': p['profiles'][profile]['channels'],
            'baseImage': base, 'baseVersion': version, 'kernel': kernel, 'image': image, 'sbomSha256': sbom_hash}


def actual_inventory(image, out):
    with out.open('w') as stream:
        subprocess.run(['podman', 'run', '--rm', '--network=none', '--read-only', '--entrypoint', '/usr/bin/rpm', image,
                        '-qa', '--qf', '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'], stdout=stream, check=True)
    return rpm.parse_inventory(out)


def installed_profile(image, expected, root):
    cid = command('podman', 'create', image, '/usr/bin/true')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile'
            subprocess.run(['podman', 'cp', cid + ':/usr/share/bazzite-tower/snitchwatch-bridge-profile', str(path)], check=True)
            require(path.read_text() == expected + '\n', 'installed bridge profile mismatch')
            result = {'profile': expected, 'trust': {}}
            for name, original in TRUST.items():
                location = {'cosign.pub': '/etc/pki/containers/bazzite-tower-cosign.pub', 'policy.json': '/etc/containers/policy.json', 'bazzite-tower.yaml': '/etc/containers/registries.d/bazzite-tower.yaml'}[name]
                subprocess.run(['podman', 'cp', cid + ':' + location, str(path)], check=True)
                require(pub.file_sha(path) == pub.file_sha(root / original), 'installed trust policy/key substitution')
                result['trust'][name] = pub.file_sha(path)
            if expected == 'system':
                for kind, binary in [('bridge', '/usr/bin/snitchwatch-bridge-cli'), ('daemon', '/usr/bin/opensnitchd')]:
                    subprocess.run(['podman', 'cp', cid + ':/usr/share/snitchwatch/system-' + kind + '-manifest.json', str(path)], check=True)
                    manifest = pub.decode(pub.read(path))
                    subprocess.run(['podman', 'cp', cid + ':' + binary, str(path)], check=True)
                    require(manifest['binary']['path'] == binary and manifest['binary']['sha256'] == pub.file_sha(path), 'installed native binary/manifest mismatch')
                    result[kind] = manifest
            return result
    finally:
        subprocess.run(['podman', 'rm', cid], check=True, stdout=subprocess.DEVNULL)


def inventory_kernel(records):
    kernels = [v + '-' + release for name, epoch, v, release, arch in records if name == 'kernel-core' and epoch == '0' and arch == 'x86_64']
    require(len(kernels) == 1, 'one installed x86_64 kernel required')
    return kernels[0]


def collect(root, data, profile, base, version):
    p = policy(root)
    require(not data.exists(), 'new artifact directory required')
    data.mkdir()
    image = 'localhost/bazzite-tower:refresh-' + profile
    write_installed = installed_profile(image, profile, root)
    (data / 'installed.json').write_bytes(pub.canonical(write_installed))
    records = actual_inventory(image, data / 'rpms.tsv')
    (data / 'sbom.json').write_bytes(pub.canonical(rpm.make_document(records, p['sourceCreated'])))
    subprocess.run(['podman', 'unshare', 'skopeo', 'copy', '--all', '--preserve-digests', 'containers-storage:' + image,
                    'oci-archive:' + str(data / 'image.oci.tar') + ':refresh'], check=True)
    proof = evidence(p, profile, base, version, pub.image_identity(data / 'image.oci.tar'), pub.file_sha(data / 'sbom.json'), inventory_kernel(records))
    proof['installedSha256'] = pub.file_sha(data / 'installed.json')
    (data / 'build-provenance.json').write_bytes(pub.canonical(proof))


def validate(root, data, profile, base, version, execute=False):
    p = policy(root)
    require(set(path.name for path in data.iterdir()) == FILES, 'unexpected artifact inventory')
    for path in data.iterdir():
        limit = 40 * 1024 ** 3 if path.name == 'image.oci.tar' else 16 * 1024 ** 2
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'unsafe/oversized artifact')
    records = rpm.parse_inventory(data / 'rpms.tsv')
    require(pub.decode(pub.read(data / 'sbom.json')) == rpm.make_document(records, p['sourceCreated']), 'RPM/SPDX substitution')
    proof = evidence(p, profile, base, version, pub.image_identity(data / 'image.oci.tar'), pub.file_sha(data / 'sbom.json'), inventory_kernel(records))
    installed = pub.decode(pub.read(data / 'installed.json'))
    require(installed['profile'] == profile and installed['trust'] == {name: pub.file_sha(root / path) for name, path in TRUST.items()}, 'installed profile/trust mismatch')
    require(set(installed) == ({'profile', 'trust', 'bridge', 'daemon'} if profile == 'system' else {'profile', 'trust'}), 'invalid installed evidence inventory')
    if profile == 'system':
        for kind, binary in [('bridge', '/usr/bin/snitchwatch-bridge-cli'), ('daemon', '/usr/bin/opensnitchd')]:
            manifest = installed[kind]
            require(manifest['profile'] == 'system' and manifest['source'] == p['nativeSource' if kind == 'bridge' else 'daemonSource'], 'installed native source substitution')
            require(manifest['binary']['path'] == binary, 'installed native binary path substitution')
            pub.hex_value(manifest['binary']['sha256'])
        require(installed['daemon']['patch']['sha256'] == p['daemonPatchSha256'], 'installed daemon patch substitution')
    proof['installedSha256'] = pub.file_sha(data / 'installed.json')
    require(pub.decode(pub.read(data / 'build-provenance.json')) == proof, 'base/source/profile/provenance substitution')
    if execute:
        # This branch belongs ONLY to the fresh job with no publish/sign/attest
        # permissions. Never call it from the privileged publisher.
        image = 'localhost/bazzite-refresh-validation:' + profile
        subprocess.run(['podman', 'unshare', 'skopeo', 'copy', '--all', '--preserve-digests',
                        'oci-archive:' + str(data / 'image.oci.tar') + ':refresh', 'containers-storage:' + image], check=True)
        require(command('podman', 'image', 'inspect', '--format', '{{.Digest}}', image) == proof['image']['manifestDigest'], 'loaded image identity mismatch')
        require(installed_profile(image, profile, root) == installed, 'actual installed profile/trust/native source mismatch')
        with tempfile.TemporaryDirectory() as tmp:
            require(actual_inventory(image, Path(tmp) / 'rpms.tsv') == records, 'actual installed RPM inventory mismatch')
    hashes = {name: pub.file_sha(data / name) for name in sorted(FILES)}
    return proof, hashes


def revalidate(root, data, profile, base, version):
    proof, hashes = validate(root, data, profile, base, version)
    require(hashes == pub.decode(os.environ['VALIDATED_HASHES'].encode()), 'validated artifact bytes changed')
    require(proof['image']['manifestDigest'] == os.environ['VALIDATED_DIGEST'], 'validated digest changed')
    return proof


def trust(root, out):
    out.mkdir(); (out / 'registries.d').mkdir()
    shutil.copyfile(root / 'cosign.pub', out / 'cosign.pub')
    value = pub.decode(pub.read(root / TRUST['policy.json']))
    rule = [{'type': 'sigstoreSigned', 'keyPath': '/etc/pki/containers/bazzite-tower-cosign.pub', 'signedIdentity': {'type': 'matchRepository'}}]
    require(value['default'] == [{'type': 'reject'}] and value['transports']['docker'][DEST] == rule, 'host trust policy mismatch')
    rule[0]['keyPath'] = str(out / 'cosign.pub')
    (out / 'policy.json').write_bytes(pub.canonical({'default': [{'type': 'reject'}], 'transports': {'docker': {DEST: rule}}}))
    shutil.copyfile(root / TRUST['bazzite-tower.yaml'], out / 'registries.d/bazzite-tower.yaml')


def fresh(root, current):
    policy(root)
    require(pub.read(root / '.github/base-refresh-policy.json') == pub.read(current), 'stale main policy: pointers must not move')


def envelopes(path):
    result = []
    for line in pub.read(path).decode().splitlines():
        if line.strip():
            raw = pub.decode(line.encode())
            for item in raw if isinstance(raw, list) else [raw]:
                result.append(pub.decode(base64.b64decode(item['payload'], validate=True)))
    return result


def attestations(data, verified, digest):
    for name, kind, file in [('sbom', 'https://spdx.dev/Document', 'sbom.json'), ('provenance', PREDICATE, 'build-provenance.json')]:
        expected = pub.decode(pub.read(data / file))
        require(any(v.get('predicateType') == kind and v.get('predicate') == expected and
                    any(s == {'name': DEST, 'digest': {'sha256': digest[7:]}} for s in v.get('subject', []))
                    for v in envelopes(verified / (name + '.json'))), 'signed predicate/subject substitution: ' + name)


def monotonic(p, proof, previous):
    require(previous.get('kind') == 'accepted-source-base-refresh' and previous.get('repository') == p['repository'] and
            previous.get('workflow') == WORKFLOW and previous.get('ref') == p['ref'] and previous.get('profile') == proof['profile'], 'previous refresh provenance identity mismatch')
    require(base_version(proof['baseVersion']) >= base_version(previous['baseVersion']), 'Bazzite downgrade below signed channel baseline')
    require(kernel_version(proof['kernel']) >= kernel_version(previous['kernel']), 'kernel downgrade below signed channel baseline')


def baseline(root, data, profile, trust_root):
    p = policy(root)
    proof = pub.decode(pub.read(data / 'build-provenance.json'))
    channel = p['profiles'][profile]['channels'][0]
    result = subprocess.run(['skopeo', 'inspect', '--raw', 'docker://' + DEST + ':' + channel], capture_output=True)
    if result.returncode:
        # Refuse auth, transport and timeout failures. This is a deliberately
        # reviewed first-publication exception for the previously absent stable.
        require(p['bootstrap'][profile]['allowMissing'] and b'manifest unknown' in result.stderr.lower(), 'cannot establish signed channel baseline')
        return
    current_digest = 'sha256:' + pub.sha(result.stdout)
    image = DEST + '@' + current_digest
    subprocess.run(['timeout', '180', 'cosign', 'verify', '--key', str(trust_root / 'cosign.pub'), image], check=True, stdout=subprocess.DEVNULL)
    if current_digest == p['bootstrap'][profile]['digest']:
        return  # Exact owner-accepted, key-signed r15.1 system digest only.
    verified = subprocess.check_output(['timeout', '180', 'cosign', 'verify-attestation', '--new-bundle-format', '--key', str(trust_root / 'cosign.pub'), '--type', PREDICATE, image])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'baseline.json'; path.write_bytes(verified)
        values = [v['predicate'] for v in envelopes(path) if v.get('predicateType') == PREDICATE and
                  any(s == {'name': DEST, 'digest': {'sha256': current_digest[7:]}} for s in v.get('subject', []))]
    require(values, 'no destination-bound signed refresh baseline')
    for previous in values:
        monotonic(p, proof, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['resolve', 'source', 'collect', 'validate', 'revalidate', 'trust', 'fresh', 'attestations', 'baseline'])
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--profile', choices=['legacy', 'system'])
    parser.add_argument('--base'); parser.add_argument('--version')
    parser.add_argument('--current', type=Path)
    args = parser.parse_args()
    os.environ['CONTROL'] = str(args.control)
    context(); p = policy(args.control)
    if args.action == 'resolve':
        # Resolve stable once. Every later pull uses only this exact digest.
        ref = p['upstream'] + ':' + p['upstreamTag']
        info = pub.decode(command('skopeo', 'inspect', '--override-os', 'linux', '--override-arch', 'amd64', 'docker://' + ref).encode())
        base = immutable_base(p['upstream'] + '@' + pub.digest(info['Digest']), p)
        info = pub.decode(command('skopeo', 'inspect', '--override-os', 'linux', '--override-arch', 'amd64', 'docker://' + base).encode())
        version = info['Labels']['org.opencontainers.image.version']
        require(base_version(version) >= base_version(p['minimumBaseVersion']), 'upstream Bazzite downgrade')
        pub.output({'base': base, 'version': version, 'source': p['sourceCommit'], 'policy': pub.file_sha(args.control / '.github/base-refresh-policy.json')})
    elif args.action == 'source': source(args.control, args.source)
    elif args.action == 'collect': collect(args.control, args.data, args.profile, args.base, args.version)
    elif args.action == 'validate':
        proof, hashes = validate(args.control, args.data, args.profile, args.base, args.version, execute=True)
        pub.output({'digest': proof['image']['manifestDigest'], 'hashes': json.dumps(hashes, separators=(',', ':'))})
    elif args.action == 'revalidate': revalidate(args.control, args.data, args.profile, args.base, args.version)
    elif args.action == 'trust': trust(args.control, args.data)
    elif args.action == 'fresh': fresh(args.control, args.current)
    elif args.action == 'baseline': baseline(args.control, args.data, args.profile, args.current)
    elif args.action == 'attestations': attestations(args.data, args.current, os.environ['VALIDATED_DIGEST'])


if __name__ == '__main__':
    main()
