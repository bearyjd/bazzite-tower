#!/usr/bin/env python3
"""Compose only verified native binary/licenses and pinned system overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, dest, mode=0o644):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('source', 'artifact', 'out', 'pins', 'verifier', 'legacy-config', 'candidate-config', 'build-log', 'repro-log', 'rpm-inventory', 'tarball', 'os-update-rule', 'resolved-dns-rule'):
        parser.add_argument('--' + arg, required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out == Path('/') or out.exists():
        raise ValueError('output must be a new isolated staging directory')
    pins = json.loads(args.pins.read_text())
    git = lambda *values: subprocess.check_output(['git', '-C', str(source), *values], text=True).strip()
    if git('rev-parse', 'HEAD') != pins['sourceCommit'] or git('rev-parse', 'HEAD^{tree}') != pins['sourceTree'] or git('status', '--porcelain', '--ignored'):
        raise ValueError('source checkout is not exact and completely clean')
    sub = source / 'vendor/opensnitch'
    if subprocess.check_output(['git', '-C', str(sub), 'rev-parse', 'HEAD'], text=True).strip() != pins['submoduleCommit']:
        raise ValueError('unexpected submodule checkout')
    for name, expected in pins['sourceFiles'].items():
        if sha(source / name) != expected:
            raise ValueError('source build/staging input differs from pin: ' + name)
    original = json.loads((args.artifact / 'usr/share/snitchwatch-bridge/MANIFEST.json').read_text())
    if original.get('version') != pins['bridgeVersion']:
        raise ValueError('native artifact does not contain the required bridge version')
    if original['source']['git_commit'] != pins['sourceCommit'] or original['source']['dirty']:
        raise ValueError('wrong/dirty native artifact source')
    legacy = json.loads(args.legacy_config.read_text())
    candidate = json.loads(args.candidate_config.read_text())
    expected_config = json.loads(json.dumps(legacy))
    expected_config['Server']['Address'] = 'unix:opensnitchd.sock'
    expected_config['DefaultAction'] = 'allow'
    if candidate != expected_config or candidate['DefaultAction'] != 'allow' or candidate['ProcMonitorMethod'] != 'proc':
        raise ValueError('candidate config must retain the allow/proc defaults and change only the daemon address')
    binary = args.artifact / 'usr/bin/snitchwatch-bridge-cli'
    binary_sha = sha(binary)
    actual_version = subprocess.check_output([str(binary), '--version'], text=True, timeout=5).strip()
    if actual_version != 'snitchwatch-bridge-cli ' + pins['bridgeVersion']:
        raise ValueError('actual binary --version differs from required bridge version')
    # Stage from this exact source checkout, never install the schema1 user unit.
    subprocess.run(['bash', str(source / 'packaging/system/stage.sh'), str(out), str(binary), binary_sha], check=True)
    # stage.sh also stages upstream's deny/ebpf daemon config. The tower keeps
    # its reviewed allow/proc baseline; eBPF requires owner testing. Drop it: a file
    # pre-placed at that path would meet the RPM's cpio extraction (mtime-
    # dependent overwrite, see 95-firewall.sh), and 95-firewall.sh installs the
    # reviewed tower candidate (allow + proc) there after extraction anyway.
    staged_config = out / 'etc/opensnitchd/default-config.json'
    if sha(staged_config) != pins['sourceFiles']['packaging/system/opensnitchd-default-config.json']:
        raise ValueError('stage.sh did not stage the pinned upstream daemon config')
    staged_config.unlink()
    licenses = args.artifact / 'usr/share/licenses/snitchwatch-bridge'
    for name in ('LICENSE', 'LICENSE.opensnitch', 'THIRD-PARTY-LICENSES.md'):
        copy(licenses / name, out / 'usr/share/licenses/snitchwatch-bridge' / name)
    copy(args.pins, out / 'usr/share/snitchwatch/build-pins.json')
    copy(args.verifier, out / 'usr/libexec/snitchwatch/verify-system-manifest.py', 0o755)
    copy(args.legacy_config, out / 'usr/share/bazzite-tower/opensnitchd-default-config.json')
    copy(args.candidate_config, out / 'usr/share/bazzite-tower/opensnitchd-system-bridge-config.json')
    copy(args.rpm_inventory, out / 'usr/share/snitchwatch/native-rpm-inventory.txt')
    # Tower-owned always-on rules: OS image updates (and the DNS they need)
    # under DefaultAction deny.  Digests are pinned in the verifier, run below.
    copy(args.os_update_rule, out / 'etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json')
    copy(args.resolved_dns_rule, out / 'etc/opensnitchd/rules/000-snitchwatch-tower-resolved-dns.json')
    for old, new in [('MANIFEST.json', 'schema1-artifact-MANIFEST.json'), ('SHA256SUMS', 'schema1-artifact-SHA256SUMS')]:
        copy(args.artifact / 'usr/share/snitchwatch-bridge' / old, out / 'usr/share/snitchwatch' / new)
    marker = out / 'usr/share/bazzite-tower/snitchwatch-bridge-profile'
    marker.write_text('system\n'); marker.chmod(0o644)
    legacy_mask = out / 'usr/lib/systemd/user/snitchwatch-bridge.service'
    legacy_mask.parent.mkdir(parents=True, exist_ok=True)
    legacy_mask.symlink_to('/dev/null')
    provenance = {
        'sourceCommit': pins['sourceCommit'], 'submoduleCommit': pins['submoduleCommit'],
        'sourceTree': git('rev-parse', 'HEAD^{tree}'),
        'builderImage': pins['builderImage'], 'artifactSha256': sha(args.tarball),
        'rpmInventorySha256': sha(args.rpm_inventory), 'reproducible': True,
        'buildLogSha256': sha(args.build_log), 'reproLogSha256': sha(args.repro_log),
        'reproCommand': 'packaging/release/repro-check.sh <first-tarball>',
        'artifactInstallScope': 'Verified binary/licenses only; original schema1 user unit and check-install excluded',
    }
    p = out / 'usr/share/snitchwatch/native-build-provenance.json'
    p.write_text(json.dumps(provenance, sort_keys=True, indent=2) + '\n'); p.chmod(0o644)
    validator = runpy.run_path(str(args.verifier))
    files = {name: sha(out / name.lstrip('/')) for name in validator['REQUIRED']}
    manifest = {
        'schemaVersion': 1, 'profile': 'system',
        'source': {'commit': pins['sourceCommit'], 'submoduleCommit': pins['submoduleCommit'], 'tree': provenance['sourceTree']},
        'binary': {'path': '/usr/bin/snitchwatch-bridge-cli', 'sha256': binary_sha, 'version': pins['bridgeVersion']},
        'files': dict(sorted(files.items())),
        'symlinks': {'/usr/lib/systemd/user/snitchwatch-bridge.service': '/dev/null'},
        'flatpak': {'appId': 'org.snitchwatch.Snitchwatch', 'profile': 'system', 'runtimeRef': pins['flatpakRuntimeRef'], 'sourceCommit': pins['sourceCommit'],
                    'manifestSha256': pins['sourceFiles']['packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'], 'finishArgs': pins['flatpakFinishArgs']},
    }
    p = out / 'usr/share/snitchwatch/system-bridge-manifest.json'
    p.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n'); p.chmod(0o644)
    validator['verify'](out)
    print(json.dumps({'stagingRoot': str(out), 'manifestSha256': sha(p), 'binarySha256': binary_sha}, sort_keys=True))


if __name__ == '__main__':
    main()
