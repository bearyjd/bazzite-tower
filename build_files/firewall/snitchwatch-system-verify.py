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
EXPECTED_PINS_SHA256 = 'a5f626448fcdc788c293a1e3181b50a09874b0c0796b8a84759a99ff2bd7e19f'
BINARY = '/usr/bin/snitchwatch-bridge-cli'
PROFILE = '/usr/share/bazzite-tower/snitchwatch-bridge-profile'
CONFIG = '/usr/share/bazzite-tower/opensnitchd-system-bridge-config.json'
LEGACY_CONFIG = '/usr/share/bazzite-tower/opensnitchd-default-config.json'
# Upstream's opensnitch.service drop-in, staged by stage.sh (Wants=/After= the
# grpc socket, WorkingDirectory=-/run/snitchwatch: fail closed if it is missing).
DROPIN = '/usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf'
# The one opensnitchd rule Snitchwatch ships, and the only path outside /usr
# this verifier reads.  /etc is mutable on a booted system, but this file is
# reserved and pinned: an admin edit or delete makes verification refuse.
FETCH_RULE = '/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json'
# The tower's own always-on rule (owner decision 2026-10-17, "always update
# os"): OS image pulls keep working under DefaultAction deny with no GUI.  Not
# an upstream file, so it is pinned here by digest rather than in build-pins.
# Source: build_files/firewall/snitchwatch-system-os-update-rule.json.
OS_UPDATE_RULE = '/etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json'
OS_UPDATE_RULE_SHA256 = '9bd06784270af7316380b3cabe42059450b64d755fc997e64a5c0c8d317c4a42'
# Its companion: OS updates need name resolution, so systemd-resolved may
# always reach DNS.  Source: build_files/firewall/snitchwatch-system-resolved-dns-rule.json.
DNS_RULE = '/etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json'
DNS_RULE_SHA256 = '8f80789e093fd98f0b2b642268477b1e68d59006e4eb1d518acfecb7e95eaa29'
TOWER_RULES = {OS_UPDATE_RULE: OS_UPDATE_RULE_SHA256, DNS_RULE: DNS_RULE_SHA256}
RESERVED_RULES = {FETCH_RULE, *TOWER_RULES}
SOURCE_FILES = {
    '/usr/share/dbus-1/services/org.snitchwatch.Scanner.service': 'packaging/scanner/org.snitchwatch.Scanner.service',
    '/usr/share/polkit-1/actions/org.snitchwatch.scanner.policy': 'packaging/polkit/org.snitchwatch.scanner.policy',
    '/usr/lib/tmpfiles.d/snitchwatch-scanner.conf': 'packaging/scanner/snitchwatch-scanner.conf',
    '/usr/lib/systemd/system/snitchwatch-system-bridge.service': 'packaging/system/snitchwatch-system-bridge.service',
    '/usr/lib/systemd/system/snitchwatch-system-bridge-grpc.socket': 'packaging/system/snitchwatch-system-bridge-grpc.socket',
    '/usr/lib/systemd/system/snitchwatch-system-bridge-gui.socket': 'packaging/system/snitchwatch-system-bridge-gui.socket',
    '/usr/lib/tmpfiles.d/snitchwatch.conf': 'packaging/system/snitchwatch.conf',
    '/usr/lib/sysusers.d/snitchwatch.conf': 'packaging/system/snitchwatch.conf.sysusers',
    DROPIN: 'packaging/system/opensnitch-snitchwatch-system-bridge.conf',
    FETCH_RULE: 'packaging/bluebuild/files/system/etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json',
}
# Opt-in Vigie endpoint-event feed (docs/RUNBOOK.md): pristine upstream copies,
# inert under /usr/share (in no unit search path, so nothing can enable them).
# The operator copies them to /etc/... after reading upstream's README.  The
# daemon Loggers snippet (opensnitchd-logger.json) is deliberately never shipped.
EVENTS_ROOT = '/usr/share/snitchwatch/endpoint-events/'
EVENTS_FILES = ('snitchwatch-endpoint-events.conf', 'snitchwatch-endpoint-events.nft',
                'snitchwatch-endpoint-events.service', 'snitchwatch-endpoint-events-nft.service')
SOURCE_FILES.update({EVENTS_ROOT + name: 'packaging/endpoint-events/' + name for name in EVENTS_FILES})
EVENTS_NAME = 'snitchwatch-endpoint-events'
EVENTS_LOGGER = 'opensnitchd-logger'
# Image-owned unit trees are always checked.  /etc is operator state on a live
# root (the opt-in enables units there), so it is only checked on a staged tree.
EVENTS_IMAGE_DIRS = ('usr/lib/systemd/system', 'usr/lib/systemd/user')
EVENTS_LOGGER_DIRS = ('etc/opensnitchd', 'usr/share/snitchwatch', 'usr/share/bazzite-tower')
EVENTS_ETC_DIRS = ('etc/systemd/system', 'etc/systemd/user', 'etc/xdg/systemd/user')
LICENSES = {'LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md'}
SCANNER_BINARIES = {'/usr/libexec/snitchwatch-scanner-privileged', '/usr/libexec/snitchwatch-scanner-broker'}
SCANNER_ROOT = '/usr/share/snitchwatch/scanner/'
SCANNER_LICENSE_ROOT = '/usr/share/licenses/snitchwatch-scanner/'
SCANNER_CRATE_MANIFESTS = ('crates/scanner-broker/Cargo.toml', 'crates/scanner-privileged/Cargo.toml')
SCANNER_CRATE_DIRS = ('crates/scanner-privileged/', 'crates/scanner-broker/')
# Every file of the two built crates at the pinned upstream commit, plus the
# file the tower patch adds.  Anything else (e.g. a new build.rs) is refused.
SCANNER_SOURCE_ALLOWLIST = frozenset({
    'crates/scanner-broker/Cargo.toml',
    'crates/scanner-broker/src/lib.rs',
    'crates/scanner-broker/src/main.rs',
    'crates/scanner-privileged/Cargo.toml',
    'crates/scanner-privileged/src/checks/kargs.rs',
    'crates/scanner-privileged/src/checks/lockdown.rs',
    'crates/scanner-privileged/src/checks/mod.rs',
    'crates/scanner-privileged/src/checks/modules.rs',
    'crates/scanner-privileged/src/checks/rootkit.rs',
    'crates/scanner-privileged/src/command.rs',
    'crates/scanner-privileged/src/error.rs',
    'crates/scanner-privileged/src/facts.rs',
    'crates/scanner-privileged/src/lib.rs',
    'crates/scanner-privileged/src/main.rs',
    'crates/scanner-privileged/src/model.rs',
    'crates/scanner-privileged/src/scan.rs',
    'crates/scanner-privileged/src/store.rs',
    'crates/scanner-privileged/tests/end_to_end.rs',
})
SCANNER_ACTIVATION_NAMES = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
SCANNER_SESSION_ACTIVATION = 'usr/share/dbus-1/services/org.snitchwatch.Scanner.service'
SCANNER_ACTIVATION_DIRS = ('usr/lib/systemd/system', 'usr/lib/systemd/user', 'etc/systemd/system', 'etc/systemd/user',
                           'etc/xdg/systemd/user', 'usr/share/dbus-1/system-services', 'etc/dbus-1/system-services',
                           'usr/share/dbus-1/services', 'etc/dbus-1/services')
SCANNER_UNIT_SUFFIXES = ('.service', '.timer', '.socket', '.path')
SCANNER_WANTS_SUFFIXES = ('.wants', '.requires')
SCANNER_EVIDENCE = {'source-inputs.json', 'dependency-closure.json', 'scanner.patch', 'build.log', 'repro.log', 'test.log',
                    'snitchwatch-scanner-privileged.ldd', 'snitchwatch-scanner-broker.ldd'}
RECEIPTS = {
    PINS, '/usr/share/snitchwatch/native-build-provenance.json',
    '/usr/share/snitchwatch/native-rpm-inventory.txt',
    '/usr/share/snitchwatch/schema1-artifact-MANIFEST.json',
    '/usr/share/snitchwatch/schema1-artifact-SHA256SUMS',
}
REQUIRED = set(SOURCE_FILES) | RECEIPTS | {
    BINARY, PROFILE, CONFIG, LEGACY_CONFIG, OS_UPDATE_RULE, DNS_RULE,
    '/usr/libexec/snitchwatch/verify-system-manifest.py',
} | {'/usr/share/licenses/snitchwatch-bridge/' + name for name in LICENSES} | SCANNER_BINARIES | {
    SCANNER_ROOT + name for name in SCANNER_EVIDENCE | {'provenance.json'}
} | {SCANNER_LICENSE_ROOT + name for name in {'LICENSE', 'THIRD-PARTY-LICENSES.md'}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def describe(name):
    if name == FETCH_RULE:
        return name + ' (the shipped, image-pinned Snitchwatch opensnitchd rule; it is reserved: restore it, do not edit or delete it)'
    if name == OS_UPDATE_RULE:
        return name + ' (the shipped, image-pinned tower OS-update opensnitchd rule; it is reserved: restore it, do not edit or delete it)'
    if name == DNS_RULE:
        return name + ' (the shipped, image-pinned tower resolver DNS opensnitchd rule; it is reserved: restore it, do not edit or delete it)'
    return name


def read_file(root, name, mode=0o644):
    if not isinstance(name, str) or not (name.startswith('/usr/') or name in RESERVED_RULES) or '..' in Path(name).parts:
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
    if root == Path('/') and (info.st_uid != 0 or info.st_gid != 0):
        raise ValueError('immutable file is not root-owned: ' + describe(name))
    return path.read_bytes()


def refuse_persistent_scanner_activation(root):
    """Only the session D-Bus activation file may reference the scanner binaries."""
    for directory in SCANNER_ACTIVATION_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            name = path.name.lower()
            relative = path.relative_to(root).as_posix()
            if relative == SCANNER_SESSION_ACTIVATION and path.is_file() and not path.is_symlink():
                continue
            in_wants = any(part.lower().endswith(SCANNER_WANTS_SUFFIXES) for part in path.relative_to(base).parts[:-1])
            nameable = name.endswith(SCANNER_UNIT_SUFFIXES + SCANNER_WANTS_SUFFIXES) or path.is_symlink() or in_wants
            if nameable and ('scanner' in name or name.startswith('dbus-org.snitchwatch.')):
                raise ValueError('unexpected persistent scanner activation: ' + relative)
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if any(value.encode() in data for value in SCANNER_ACTIVATION_NAMES):
                raise ValueError('unexpected persistent scanner activation: ' + relative)


def refuse_enabled_endpoint_events(root, live=False):
    """The endpoint-event units are never enabled or placed in a unit search path."""
    for directory in EVENTS_IMAGE_DIRS + (() if live else EVENTS_ETC_DIRS):
        base = root / directory
        if base.is_dir():
            for path in sorted(base.rglob('*')):
                if EVENTS_NAME in path.name.lower():
                    raise ValueError('endpoint-events unit must not be installed or enabled in the image: ' + path.relative_to(root).as_posix())


def refuse_logger_snippet(root):
    """No logger snippet in the image-owned daemon/Snitchwatch trees: the feed is operator opt-in."""
    for directory in EVENTS_LOGGER_DIRS:
        base = root / directory
        for path in sorted(base.rglob('*')) if base.is_dir() else ():
            if EVENTS_LOGGER in path.name.lower():
                raise ValueError('opensnitchd logger snippet must not be shipped: ' + path.relative_to(root).as_posix())


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
        mode = 0o755 if name in {BINARY, '/usr/libexec/snitchwatch/verify-system-manifest.py'} | SCANNER_BINARIES else 0o644
        contents[name] = read_file(root, name, mode)
        if digest(contents[name]) != expected:
            raise ValueError('immutable file hash mismatch: ' + describe(name))
    for name in (CONFIG, LEGACY_CONFIG):
        if 'Loggers' in json.loads(contents[name]).get('Server', {}):
            raise ValueError('default daemon config must not have a Loggers entry (endpoint events are operator opt-in): ' + name)
    if manifest.get('binary') != {'path': BINARY, 'sha256': files[BINARY], 'version': pins['bridgeVersion']}:
        raise ValueError('binary identity differs from immutable inventory')
    if contents[BINARY][:6] != b'\x7fELF\x02\x01':
        raise ValueError('native bridge is not a little-endian ELF64 executable')
    for name in SCANNER_BINARIES:
        if contents[name][:6] != b'\x7fELF\x02\x01':
            raise ValueError('native scanner is not a little-endian ELF64 executable')
    if contents[PROFILE] != b'system\n':
        raise ValueError('incorrect bridge profile marker')
    if manifest.get('symlinks') != {'/usr/lib/systemd/user/snitchwatch-bridge.service': '/dev/null'}:
        raise ValueError('incorrect legacy user-unit mask inventory')
    if (root / 'usr/lib/systemd/user/snitchwatch-bridge.service').readlink() != Path('/dev/null'):
        raise ValueError('legacy user unit is not image-masked')
    for name, original in SOURCE_FILES.items():
        if files[name] != pins['sourceFiles'][original]:
            raise ValueError('system overlay differs from pinned source: ' + describe(name))
    for name, expected in TOWER_RULES.items():
        if files[name] != expected:
            raise ValueError('tower rule differs from the reviewed digest: ' + describe(name))
    if files[LEGACY_CONFIG] != pins['legacyConfigSha256']:
        raise ValueError('legacy image-intent reference differs from pinned migration baseline')
    config = json.loads(contents[CONFIG])
    expected_config = json.loads(contents[LEGACY_CONFIG])
    expected_config['Server']['Address'] = 'unix:opensnitchd.sock'
    # Both profiles default to allow; deny enforcement requires explicit opt-in.
    # proc stays (eBPF fails on this laptop's kernel, snitchwatch#6).
    expected_config['DefaultAction'] = 'allow'
    if config != expected_config:
        raise ValueError('system candidate must retain the allow/proc defaults and change only the daemon address')
    if config.get('DefaultAction') != 'allow' or config.get('ProcMonitorMethod') != 'proc' or config.get('Server', {}).get('Address') != 'unix:opensnitchd.sock':
        raise ValueError('incorrect system candidate policy/transport')
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
    scanner = json.loads(contents[SCANNER_ROOT + 'provenance.json'])
    if (scanner.get('schemaVersion') != 1 or scanner.get('sourceCommit') != source['commit']
            or scanner.get('sourceTree') != source['tree'] or scanner.get('builderImage') != pins['builderImage']
            or scanner.get('patchSha256') != pins['scannerPatch']['sha256']
            or scanner.get('patchedFiles') != pins['scannerPatch']['patchedFiles']
            or scanner.get('cargoLockSha256') != pins['sourceFiles']['Cargo.lock']
            or scanner.get('cargoTomlSha256') != pins['sourceFiles']['Cargo.toml']
            or not scanner.get('rustcVv', '').startswith('rustc ' + pins['rustVersion'] + ' ')
            or scanner.get('reproducible') is not True or scanner.get('testsPassed') is not True
            or scanner.get('rpmInventorySha256') != files['/usr/share/snitchwatch/native-rpm-inventory.txt']):
        raise ValueError('scanner build/source/patch/test provenance mismatch')
    if scanner.get('buildCommand') != ['cargo', 'build', '--release', '--locked', '--offline', '-p', 'scanner-privileged', '-p', 'scanner-broker']:
        raise ValueError('unexpected scanner build command')
    binaries = {Path(name).name: files[name] for name in SCANNER_BINARIES}
    if scanner.get('binaries') != binaries:
        raise ValueError('scanner executable differs from build provenance')
    evidence = {name: files[SCANNER_ROOT + name] for name in SCANNER_EVIDENCE}
    evidence.update({name: files[SCANNER_LICENSE_ROOT + name] for name in {'LICENSE', 'THIRD-PARTY-LICENSES.md'}})
    if scanner.get('evidence') != evidence or files[SCANNER_ROOT + 'scanner.patch'] != pins['scannerPatch']['sha256']:
        raise ValueError('scanner evidence differs from build provenance')
    inputs = json.loads(contents[SCANNER_ROOT + 'source-inputs.json'])
    expected_inputs = {**{name: pins['sourceFiles'][name] for name in ('Cargo.toml', 'Cargo.lock') + SCANNER_CRATE_MANIFESTS}, **pins['scannerPatch']['patchedFiles']}
    for name, expected in expected_inputs.items():
        if inputs.get(name) != expected:
            raise ValueError('scanner patched source input mismatch')
    # The complete upstream tree is not derivable from the pins, so the built
    # crates and build-affecting config are restricted to an explicit allowlist.
    for name in inputs:
        if ((name.startswith(SCANNER_CRATE_DIRS) and name not in SCANNER_SOURCE_ALLOWLIST)
                or name.startswith('.cargo/') or Path(name).name.startswith('rust-toolchain')):
            raise ValueError('unexpected scanner source input: ' + name)
    closure = json.loads(contents[SCANNER_ROOT + 'dependency-closure.json'])
    if set(closure) != set(binaries) or any(not isinstance(value, list) or not value for value in closure.values()):
        raise ValueError('incomplete scanner dependency closure')
    # Session D-Bus activation is the sole scanner activation mechanism.
    refuse_persistent_scanner_activation(root)
    refuse_enabled_endpoint_events(root, live=root == Path('/'))
    refuse_logger_snippet(root)
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
