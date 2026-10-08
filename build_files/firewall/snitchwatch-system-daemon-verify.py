#!/usr/bin/env python3
"""Read-only verification of the reviewed system-only native daemon."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MANIFEST = '/usr/share/snitchwatch/system-daemon-manifest.json'
ROOT = '/usr/share/snitchwatch/daemon/'
PINS = ROOT + 'build-pins.json'
PATCH = ROOT + 'opensnitch-shutdown-repair.patch'
CANDIDATE = ROOT + 'opensnitchd'
BINARY = '/usr/bin/opensnitchd'
VERIFIER = '/usr/libexec/snitchwatch/verify-system-daemon.py'
EXPECTED_PINS_SHA256 = '3377267c8d991e84dfa365fbf2581a9ead2e1d3e743bd101ec965cd1a7afb6b5'
REQUIRED = {PINS, PATCH, CANDIDATE, VERIFIER, ROOT + 'native-build-provenance.json', ROOT + 'rpm-inventory.txt', ROOT + 'go-build-info.txt',
            ROOT + 'generated/ui.pb.go', ROOT + 'generated/ui_grpc.pb.go', ROOT + 'source/go.mod', ROOT + 'source/go.sum', ROOT + 'source/ui.proto',
            '/usr/share/licenses/snitchwatch-opensnitchd/LICENSE', '/usr/share/licenses/snitchwatch-opensnitchd/THIRD-PARTY-LICENSES.md'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(root, name, mode=0o644):
    if not isinstance(name, str) or not name.startswith('/usr/') or '..' in Path(name).parts:
        raise ValueError('invalid daemon immutable path')
    path = root / name.lstrip('/')
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError('symlink in daemon immutable path: ' + name)
        cursor = cursor.parent
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('incorrect daemon file type/mode: ' + name)
    return path.read_bytes()


def verify(root, stage_only=False):
    if stage_only and root == Path('/'):
        raise ValueError('stage-only is forbidden for the production root')
    manifest = json.loads(read_file(root, MANIFEST))
    pins_bytes = read_file(root, PINS)
    if digest(pins_bytes) != EXPECTED_PINS_SHA256:
        raise ValueError('daemon pins differ from reviewed image inputs')
    pins = json.loads(pins_bytes)
    if manifest.get('schemaVersion') != 1 or manifest.get('profile') != 'system':
        raise ValueError('unsupported daemon manifest schema/profile')
    source = {'commit': pins['sourceCommit'], 'tree': pins['sourceTree']}
    if manifest.get('source') != source or manifest.get('patch') != {'path': PATCH, 'sha256': pins['patchSha256']}:
        raise ValueError('daemon source/patch identity mismatch')
    files = manifest.get('files', {})
    if set(files) != REQUIRED:
        raise ValueError('incomplete or unexpected daemon immutable inventory')
    contents = {}
    for name, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('invalid daemon immutable digest')
        contents[name] = read_file(root, name, 0o755 if name in {CANDIDATE, VERIFIER} else 0o644)
        if digest(contents[name]) != expected:
            raise ValueError('daemon immutable hash mismatch: ' + name)
    if manifest.get('binary') != {'path': BINARY, 'candidatePath': CANDIDATE, 'sha256': files[CANDIDATE], 'version': pins['daemonVersion']}:
        raise ValueError('daemon binary identity mismatch')
    if contents[CANDIDATE][:6] != b'\x7fELF\x02\x01':
        raise ValueError('native daemon is not little-endian ELF64')
    if not stage_only and digest(read_file(root, BINARY, 0o755)) != files[CANDIDATE]:
        raise ValueError('installed daemon differs from reviewed native candidate')
    checks = {PATCH: pins['patchSha256'], ROOT + 'source/ui.proto': pins['protoSourceSha256'],
              '/usr/share/licenses/snitchwatch-opensnitchd/LICENSE': pins['licenseSha256']}
    checks.update({ROOT + 'source/' + Path(name).name: value for name, value in pins['moduleLocks'].items()})
    checks.update({ROOT + 'generated/' + name: value for name, value in pins['generatedFiles'].items()})
    for name, expected in checks.items():
        if files[name] != expected:
            raise ValueError('daemon source/module/protocol/license differs from pin: ' + name)
    provenance = json.loads(contents[ROOT + 'native-build-provenance.json'])
    expected = {'sourceCommit': source['commit'], 'sourceTree': source['tree'], 'patchSha256': pins['patchSha256'], 'patchedFiles': pins['patchedFiles'],
                'builderImage': pins['builderImage'], 'goVersion': pins['goVersion'], 'buildCommand': pins['buildCommand'], 'reproducible': True,
                'firstBinarySha256': files[CANDIDATE], 'secondBinarySha256': files[CANDIDATE],
                'rpmInventorySha256': files[ROOT + 'rpm-inventory.txt'], 'generatedFiles': pins['generatedFiles'], 'tools': pins['tools'], 'actualVersionOutput': pins['daemonVersion']}
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError('daemon build/reproduction provenance differs from pins')
    info = contents[ROOT + 'go-build-info.txt'].decode()
    if pins['goVersion'].split()[2] not in info.splitlines()[0]:
        raise ValueError('daemon Go build-info toolchain mismatch')
    flags = dict(line.split()[1].split('=', 1) for line in info.splitlines() if line.split()[:1] == ['build'] and '=' in line.split()[1])
    if flags.get('-trimpath') != 'true' or flags.get('vcs.revision') != source['commit'] or flags.get('vcs.modified') != 'true':
        raise ValueError('daemon build-info lacks pinned upstream+reviewed patch provenance')
    modules = provenance.get('linkedModules', [])
    if not modules or not contents['/usr/share/licenses/snitchwatch-opensnitchd/THIRD-PARTY-LICENSES.md'].startswith(b'# Linked Go module licenses\n'):
        raise ValueError('daemon linked module licenses missing')
    for module in modules:
        if not module.get('licenses') or ('Module checksum: `' + module['sum'] + '`').encode() not in contents['/usr/share/licenses/snitchwatch-opensnitchd/THIRD-PARTY-LICENSES.md']:
            raise ValueError('daemon linked module license provenance mismatch')
    return {'profile': 'system', 'sourceCommit': source['commit'], 'patchSha256': pins['patchSha256'], 'binaryPath': BINARY,
            'binarySha256': files[CANDIDATE], 'verifiedFiles': len(files), 'installedBinaryChecked': not stage_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--stage-only', action='store_true', help='isolated image overlay before RPM binary replacement only')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root.resolve(), args.stage_only), sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        print('system-daemon: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
