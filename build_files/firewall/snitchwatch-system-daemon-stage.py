#!/usr/bin/env python3
"""Check the reviewed daemon inputs and stage a reproducible native overlay."""
import argparse
import hashlib
import json
import os
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


def check_source(source, pins, patch, patched):
    git = lambda *args: subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
    if git('rev-parse', 'HEAD') != pins['sourceCommit'] or git('rev-parse', 'HEAD^{tree}') != pins['sourceTree']:
        raise ValueError('daemon upstream Git identity mismatch')
    if sha(patch) != pins['patchSha256']:
        raise ValueError('daemon patch differs from independent review')
    checks = dict(pins['moduleLocks'])
    checks.update({'proto/ui.proto': pins['protoSourceSha256'], 'LICENSE': pins['licenseSha256']})
    if patched:
        checks.update(pins['patchedFiles'])
        checks.update({'daemon/ui/protocol/' + name: value for name, value in pins['generatedFiles'].items()})
    # Git's normal porcelain omits ignored files. Go also compiles ignored
    # .go/C sources, so inspect every changed and untracked path, including
    # generated ignored files, before any build or staging claim.
    inventory = set()
    for args in [('diff', '--name-only', '-z', 'HEAD'),
                 ('ls-files', '--others', '--exclude-standard', '-z'),
                 ('ls-files', '--others', '--ignored', '--exclude-standard', '-z')]:
        inventory.update(name for name in subprocess.check_output(['git', '-C', str(source), *args]).decode().split('\0') if name)
    allowed = set(pins['patchedFiles']) | {'daemon/ui/protocol/' + name for name in pins['generatedFiles']} if patched else set()
    if inventory != allowed:
        raise ValueError('daemon source changes are not exactly the reviewed patch/generated inputs')
    for name, expected in checks.items():
        if sha(source / name) != expected:
            raise ValueError('daemon source input mismatch: ' + name)


def license_report(source, work):
    """Include the complete top-level licenses of every linked Go module."""
    info = (work / 'go-build-info.txt').read_text()
    modules = []
    for line in info.splitlines():
        fields = line.split()
        if fields and fields[0] == 'dep':
            modules.append(fields[1:4])
    report = ['# Linked Go module licenses', '', 'Built with the locked upstream go.mod/go.sum; each module checksum follows.', '']
    recorded = []
    for name, version, checksum in sorted(modules):
        meta = json.loads(subprocess.check_output(['go', 'list', '-mod=readonly', '-m', '-json', name], cwd=source / 'daemon', text=True))
        if (meta['Path'], meta['Version'], meta['Sum']) != (name, version, checksum):
            raise ValueError('linked module identity differs from module cache')
        directory = Path(meta['Dir']).resolve()
        cache = Path(os.environ['GOMODCACHE']).resolve()
        if not directory.is_relative_to(cache):
            raise ValueError('license module escapes declared module cache')
        licenses = sorted(p for p in directory.iterdir() if p.is_file() and not p.is_symlink() and p.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')))
        if not licenses:
            raise ValueError('linked module has no top-level license: ' + name)
        report.extend(['## ' + name + ' ' + version, '', 'Module checksum: `' + checksum + '`', ''])
        for path in licenses:
            report.extend(['### ' + path.name, '', path.read_text(errors='strict').rstrip(), ''])
        recorded.append({'path': name, 'version': version, 'sum': checksum, 'licenses': {p.name: sha(p) for p in licenses}})
    if not recorded:
        raise ValueError('native daemon build-info contains no linked Go modules')
    return '\n'.join(report) + '\n', recorded


def stage(args, pins):
    source, work, out = args.source.resolve(), args.work.resolve(), args.out.resolve()
    if out == Path('/') or (out / 'usr/share/snitchwatch/system-daemon-manifest.json').exists():
        raise ValueError('daemon output must be an isolated new overlay')
    check_source(source, pins, args.patch, True)
    first, second = work / 'opensnitchd-first', work / 'opensnitchd-second'
    if first.read_bytes() != second.read_bytes():
        raise ValueError('fresh daemon builds are not byte-identical')
    version = subprocess.check_output([str(first), '-version'], text=True, timeout=5).strip()
    if version != pins['daemonVersion']:
        raise ValueError('actual native daemon version differs from pin')
    root = out / 'usr/share/snitchwatch/daemon'
    copy(first, root / 'opensnitchd', 0o755)
    copy(args.pins, root / 'build-pins.json')
    copy(args.patch, root / 'opensnitch-shutdown-repair.patch')
    copy(args.verifier, out / 'usr/libexec/snitchwatch/verify-system-daemon.py', 0o755)
    copy(work / 'rpm-inventory.txt', root / 'rpm-inventory.txt')
    copy(work / 'go-build-info.txt', root / 'go-build-info.txt')
    for name in pins['generatedFiles']:
        copy(source / 'daemon/ui/protocol' / name, root / 'generated' / name)
    for name in ('go.mod', 'go.sum'):
        copy(source / 'daemon' / name, root / 'source' / name)
    copy(source / 'proto/ui.proto', root / 'source/ui.proto')
    licenses = out / 'usr/share/licenses/snitchwatch-opensnitchd'
    copy(source / 'LICENSE', licenses / 'LICENSE')
    report, modules = license_report(source, work)
    licenses.mkdir(parents=True, exist_ok=True)
    path = licenses / 'THIRD-PARTY-LICENSES.md'
    path.write_text(report); path.chmod(0o644)
    provenance = {'sourceCommit': pins['sourceCommit'], 'sourceTree': pins['sourceTree'], 'patchSha256': pins['patchSha256'],
                  'patchedFiles': pins['patchedFiles'], 'builderImage': pins['builderImage'], 'goVersion': pins['goVersion'],
                  'buildCommand': pins['buildCommand'], 'reproducible': True, 'firstBinarySha256': sha(first), 'secondBinarySha256': sha(second),
                  'rpmInventorySha256': sha(work / 'rpm-inventory.txt'), 'buildLogSha256': {name: sha(work / ('build-' + name + '.log')) for name in ('first', 'second')},
                  'generatedFiles': pins['generatedFiles'], 'tools': pins['tools'], 'linkedModules': modules,
                  'toolBinarySha256': {name: sha(work / 'build-tools' / name) for name in pins['tools']},
                  'actualVersionOutput': version, 'compileScope': 'Two independent source directories and fresh Go caches; shared verified module archives only'}
    path = root / 'native-build-provenance.json'
    path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + '\n'); path.chmod(0o644)
    validator = runpy.run_path(str(args.verifier))
    files = {name: sha(out / name.lstrip('/')) for name in validator['REQUIRED']}
    manifest = {'schemaVersion': 1, 'profile': 'system', 'source': {'commit': pins['sourceCommit'], 'tree': pins['sourceTree']},
                'patch': {'path': '/usr/share/snitchwatch/daemon/opensnitch-shutdown-repair.patch', 'sha256': pins['patchSha256']},
                'binary': {'path': '/usr/bin/opensnitchd', 'candidatePath': '/usr/share/snitchwatch/daemon/opensnitchd', 'sha256': sha(first), 'version': version},
                'files': dict(sorted(files.items()))}
    path = out / 'usr/share/snitchwatch/system-daemon-manifest.json'
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n'); path.chmod(0o644)
    validator['verify'](out, stage_only=True)
    print(json.dumps({'stagingRoot': str(out), 'binarySha256': sha(first), 'manifestSha256': sha(path)}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check-source', 'stage'))
    for name in ('pins', 'source', 'patch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--patched', action='store_true')
    for name in ('work', 'out', 'verifier'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    pins = json.loads(args.pins.read_text())
    if args.command == 'check-source':
        check_source(args.source, pins, args.patch, args.patched)
    else:
        if any(getattr(args, name) is None for name in ('work', 'out', 'verifier')):
            parser.error('stage requires --work/--out/--verifier')
        stage(args, pins)


if __name__ == '__main__':
    main()
