#!/usr/bin/env python3
"""Read-only verification of an immutable system-bridge installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MANIFEST = '/usr/share/snitchwatch/system-bridge-manifest.json'
PINS = '/usr/share/snitchwatch/build-pins.json'
EXPECTED_PINS_SHA256 = '2ff98aba31e85f60716b043d53edc3627f47a47ba83797397352a3a9b5b3a7d9'
BINARY = '/usr/bin/snitchwatch-bridge-cli'
PROFILE = '/usr/share/bazzite-tower/snitchwatch-bridge-profile'
CONFIG = '/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'
LEGACY_CONFIG = '/usr/share/bazzite-tower/opensnitchd-default-config.json'
DROPIN = '/usr/lib/systemd/system/opensnitch.service.d/20-system-bridge.conf'
# The one opensnitchd rule Snitchwatch ships, and the only path outside /usr
# this verifier reads.  /etc is mutable on a booted system, but this file is
# reserved and pinned: an admin edit or delete makes verification refuse.
FETCH_RULE = '/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json'
SOURCE_FILES = {
    '/usr/lib/systemd/system/snitchwatch-system-bridge.service': 'packaging/system/snitchwatch-system-bridge.service',
    '/usr/lib/systemd/system/snitchwatch-system-bridge-grpc.socket': 'packaging/system/snitchwatch-system-bridge-grpc.socket',
    '/usr/lib/systemd/system/snitchwatch-system-bridge-gui.socket': 'packaging/system/snitchwatch-system-bridge-gui.socket',
    '/usr/lib/tmpfiles.d/snitchwatch.conf': 'packaging/system/snitchwatch.conf',
    '/usr/lib/sysusers.d/snitchwatch.conf': 'packaging/system/snitchwatch.conf.sysusers',
    FETCH_RULE: 'packaging/bluebuild/files/system/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json',
}
LICENSES = {'LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md'}
RECEIPTS = {
    PINS, '/usr/share/snitchwatch/native-build-provenance.json',
    '/usr/share/snitchwatch/native-rpm-inventory.txt',
    '/usr/share/snitchwatch/schema1-artifact-MANIFEST.json',
    '/usr/share/snitchwatch/schema1-artifact-SHA256SUMS',
}
REQUIRED = set(SOURCE_FILES) | RECEIPTS | {
    BINARY, PROFILE, CONFIG, LEGACY_CONFIG, DROPIN,
    '/usr/share/bazzite-tower/snitchwatch/opensnitch.service.d/20-system-bridge.conf',
    '/usr/libexec/snitchwatch/verify-system-manifest.py',
} | {'/usr/share/licenses/snitchwatch-bridge/' + name for name in LICENSES}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def describe(name):
    if name == FETCH_RULE:
        return name + ' (the shipped, image-pinned Snitchwatch opensnitchd rule; it is reserved: restore it, do not edit or delete it)'
    return name


def read_file(root, name, mode=0o644):
    if not isinstance(name, str) or not (name.startswith('/usr/') or name == FETCH_RULE) or '..' in Path(name).parts:
        raise ValueError('invalid immutable file path')
    path = root / name.lstrip('/')
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError('symlink in immutable file path: ' + describe(name))
        cursor = cursor.parent
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ValueError('missing immutable file: ' + describe(name)) from None
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('incorrect file type/mode: ' + describe(name))
    return path.read_bytes()


def verify(root):
    manifest = json.loads(read_file(root, MANIFEST))
    pins_bytes = read_file(root, PINS)
    if digest(pins_bytes) != EXPECTED_PINS_SHA256:
        raise ValueError('build pins differ from reviewed integration inputs')
    pins = json.loads(pins_bytes)
    if manifest.get('schemaVersion') != 1 or manifest.get('profile') != 'system':
        raise ValueError('unsupported system manifest schema/profile')
    source = manifest.get('source', {})
    if source.get('commit') != pins['sourceCommit'] or source.get('submoduleCommit') != pins['submoduleCommit'] or source.get('tree') != pins['sourceTree']:
        raise ValueError('source identity differs from build pins')
    for key in ('commit', 'submoduleCommit', 'tree'):
        if not re.fullmatch('[0-9a-f]{40}', source.get(key, '')):
            raise ValueError('invalid source identity')
    files = manifest.get('files', {})
    if set(files) != REQUIRED:
        raise ValueError('incomplete or unexpected immutable file inventory')
    contents = {}
    for name, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('invalid immutable file digest')
        mode = 0o755 if name in {BINARY, '/usr/libexec/snitchwatch/verify-system-manifest.py'} else 0o644
        contents[name] = read_file(root, name, mode)
        if digest(contents[name]) != expected:
            raise ValueError('immutable file hash mismatch: ' + describe(name))
    if manifest.get('binary') != {'path': BINARY, 'sha256': files[BINARY], 'version': pins['bridgeVersion']}:
        raise ValueError('binary identity differs from immutable inventory')
    if contents[BINARY][:6] != b'\x7fELF\x02\x01':
        raise ValueError('native bridge is not a little-endian ELF64 executable')
    if contents[PROFILE] != b'system\n':
        raise ValueError('incorrect bridge profile marker')
    if manifest.get('symlinks') != {'/usr/lib/systemd/user/snitchwatch-bridge.service': '/dev/null'}:
        raise ValueError('incorrect legacy user-unit mask inventory')
    if (root / 'usr/lib/systemd/user/snitchwatch-bridge.service').readlink() != Path('/dev/null'):
        raise ValueError('legacy user unit is not image-masked')
    for name, original in SOURCE_FILES.items():
        if files[name] != pins['sourceFiles'][original]:
            raise ValueError('system overlay differs from pinned source: ' + describe(name))
    if files[LEGACY_CONFIG] != pins['legacyConfigSha256']:
        raise ValueError('legacy image-intent reference differs from pinned migration baseline')
    config = json.loads(contents[CONFIG])
    expected_config = json.loads(contents[LEGACY_CONFIG])
    expected_config['Server']['Address'] = 'unix:opensnitchd.sock'
    if config != expected_config:
        raise ValueError('system candidate changes more than the daemon address')
    if config.get('DefaultAction') != 'allow' or config.get('ProcMonitorMethod') != 'proc' or config.get('Server', {}).get('Address') != 'unix:opensnitchd.sock':
        raise ValueError('incorrect system candidate policy/transport')
    expected_dropin = b'[Unit]\nWants=snitchwatch-system-bridge-grpc.socket\nAfter=snitchwatch-system-bridge-grpc.socket\n\n[Service]\nWorkingDirectory=/run/snitchwatch\n'
    if contents[DROPIN] != expected_dropin or contents['/usr/share/bazzite-tower/snitchwatch/opensnitch.service.d/20-system-bridge.conf'] != expected_dropin:
        raise ValueError('incorrect daemon system-socket ordering/drop-in')
    artifact = json.loads(contents['/usr/share/snitchwatch/schema1-artifact-MANIFEST.json'])
    if artifact.get('version') != pins['bridgeVersion']:
        raise ValueError('native artifact bridge version differs from required version')
    if artifact.get('schema_version') != 1 or artifact.get('source', {}).get('git_commit') != pins['sourceCommit'] or artifact['source'].get('dirty') is not False:
        raise ValueError('incorrect original native artifact identity')
    if artifact.get('upstream', {}).get('opensnitch_commit') != pins['submoduleCommit']:
        raise ValueError('incorrect native artifact submodule')
    build = artifact.get('build', {})
    if build.get('builder_image') != pins['builderImage'] or not build.get('rustc_vv', '').startswith('rustc ' + pins['rustVersion'] + ' ') or build.get('protoc_version') != 'libprotoc ' + pins['protocVersion']:
        raise ValueError('native artifact toolchain differs from build pins')
    original_files = {entry['path']: entry for entry in artifact.get('files', [])}
    for name in {BINARY} | {'/usr/share/licenses/snitchwatch-bridge/' + value for value in LICENSES}:
        if original_files.get(name.lstrip('/'), {}).get('sha256') != files[name]:
            raise ValueError('installed binary/license differs from original artifact')
    provenance = json.loads(contents['/usr/share/snitchwatch/native-build-provenance.json'])
    if provenance.get('sourceCommit') != source['commit'] or provenance.get('submoduleCommit') != source['submoduleCommit'] or provenance.get('sourceTree') != source['tree'] or provenance.get('reproducible') is not True:
        raise ValueError('native build/repro provenance mismatch')
    if provenance.get('rpmInventorySha256') != files['/usr/share/snitchwatch/native-rpm-inventory.txt']:
        raise ValueError('native RPM inventory mismatch')
    expected_gui = {'appId': 'org.snitchwatch.Snitchwatch', 'profile': 'system', 'runtimeRef': pins['flatpakRuntimeRef'], 'sourceCommit': pins['sourceCommit'], 'manifestSha256': pins['sourceFiles']['packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'], 'finishArgs': pins['flatpakFinishArgs']}
    if manifest.get('flatpak') != expected_gui:
        raise ValueError('incorrect expected GUI system profile')
    return {'profile': 'system', 'sourceCommit': source['commit'], 'binarySha256': files[BINARY], 'verifiedFiles': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='image staging root; default /')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root.resolve()), sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('system-manifest: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
