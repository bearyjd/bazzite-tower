#!/usr/bin/env python3
"""Per-kernel OpenSnitch eBPF module build (Track A).

Subcommands (each refusal prints one `ebpf:` line and exits 1):
  kernel-export [--root R] NEW-OUT
        stage ebpf-kernel (FROM ${BASE_IMAGE}, offline): refuse unless rpm
        kernel-core, rpm kernel-devel, the one /usr/src/kernels/<r> and the one
        /usr/lib/modules/<r> agree; then write a deterministic tarball of the
        header tree plus kernel-facts.json. Never a Fedora-header fallback.
        The headers' .config must be byte-identical to /usr/lib/modules/<r>/config.
  check-pins PINS
        refuse unless the eBPF pins match EXPECTED_EBPF_PINS_SHA256 (ebpf-prep runs
        this before installing anything; build and stage re-check it).
  check-rpm --pins P RPM
        95-firewall.sh, legacy only: refuse unless the downloaded daemon RPM (name, version,
        release, sha256) is the pinned v1.8.0 release the modules are built from.
  source-pin PINS
        legacy prep: print the pinned source repository and commit (after the pairing check).
  build --pins P [--daemon-pins D --native-pins N] --profile system|legacy --repo OPENSNITCH
        --kernel HANDOFF --work NEW-DIR --out NEW-OUT
        (legacy pairs with the upstream daemon RPM pinned in P instead of D and N)
        stages snitchwatch-ebpf and snitchwatch-ebpf-rebuild (offline): unpack the
        checked headers, extract ebpf_prog as raw git blobs from the already
        verified prep checkout, check its tree sha256 and the pinned toolchain,
        compile with the upstream Makefile, strip, and write the objects.
  stage --pins P --daemon-pins D --native-pins N --profile system --kernel-facts F HANDOFF NEW-OUT
        assembly (offline): build the image overlay and ebpf-manifest.json from
        the pins, the kernel facts (F, copied straight from ebpf-kernel, must be
        byte-identical to the compile handoff's copy) and hashes computed here.

The running kernel inside a build container is the build host's; nothing here asks for it.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

# Bump together with snitchwatch-system-ebpf-pins.json (tests/test-snitchwatch-ebpf.py).
EXPECTED_EBPF_PINS_SHA256 = '9294eecd2245ecebc83d6c690f7916fc6887d37bc83f2cdd914c0a69a83f9b72'
RELEASE = re.compile(r'[0-9][0-9A-Za-z._+~-]*\.x86_64')
HEX40 = re.compile('[0-9a-f]{40}')
HEX64 = re.compile('[0-9a-f]{64}')
OBJECTS_DIR = '/usr/lib/opensnitchd/ebpf'
STAGED = 'usr/share/bazzite-tower'
BUILT_PROFILES = ('system', 'legacy')
# The legacy daemon is the upstream release RPM (95-firewall.sh), not a source build. Its modules may only be
# built from the commit its release tag peels to; this table is reviewed code, so editing the pins alone cannot re-pair.
LEGACY_RPM_COMMITS = {'1.8.0': 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc'}
# Bounds for the header handoff (the 7.2/7.3 trees are ~125 MiB, ~21.5k files).
MAX_HEADERS = 512 * 1024 * 1024
SAFE_ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC', 'SOURCE_DATE_EPOCH': '0'}


class Refusal(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def kernel_release(core, devel, src_dirs, module_dirs):
    """The one kernel release BASE_IMAGE ships, or a Refusal naming every version involved.

    CORE and DEVEL are (returncode, stdout) of `rpm -q --qf '%{VERSION}-%{RELEASE}.%{ARCH}\\n'`."""
    if core[0] != 0:
        raise Refusal('kernel-core is not installed in BASE_IMAGE: %s' % core[1].strip())
    cores = core[1].split()
    if len(cores) != 1:
        raise Refusal('BASE_IMAGE must carry exactly one kernel-core, found: %s' % (', '.join(cores) or 'none'))
    release = cores[0]
    if not RELEASE.fullmatch(release) or '..' in release:
        raise Refusal('kernel-core release %r is not an x86_64 kernel release' % release)
    if devel[0] != 0:
        raise Refusal('kernel-devel is not installed in BASE_IMAGE (kernel-core is %s); refusing: Fedora '
                      'kernel-devel/kernel-headers are not a substitute for the matching OGC headers' % release)
    devels = devel[1].split()
    if len(devels) != 1:
        raise Refusal('BASE_IMAGE must carry exactly one kernel-devel, found: %s (kernel-core %s)' % (', '.join(devels), release))
    if devels[0] != release:
        raise Refusal('kernel-devel %s does not match kernel-core %s' % (devels[0], release))
    for label, names in (('/usr/src/kernels', src_dirs), ('/usr/lib/modules', module_dirs)):
        if len(names) != 1:
            raise Refusal('%s must hold exactly one kernel, found: %s (kernel-core %s)' % (label, ', '.join(names) or 'none', release))
        if names[0] != release:
            raise Refusal('%s/%s does not match kernel-core/kernel-devel %s' % (label, names[0], release))
    return release


def query_rpm(name):
    result = subprocess.run(['rpm', '-q', '--qf', '%{VERSION}-%{RELEASE}.%{ARCH}\\n', name], capture_output=True, text=True)
    return result.returncode, result.stdout


def listing(path):
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def tree_sha256(root):
    """snitchwatch packaging/ebpf/pins.env method: sha256 of the sorted `sha256sum ./path` list of regular files."""
    lines = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in files:
            path = os.path.join(base, name)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise Refusal('ebpf_prog tree holds a non-regular file: %s' % path)
            rel = './' + os.path.relpath(path, root)
            lines.append((rel.encode(), sha256(Path(path).read_bytes())))
    lines.sort()
    return sha256(b''.join(b'%s  %s\n' % (digest.encode(), rel) for rel, digest in lines))


def link_inside(rel, target):
    """True if a relative symlink at REL (inside the tree) resolves inside the tree."""
    if os.path.isabs(target):
        return False
    resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
    return resolved != '..' and not resolved.startswith('../') and not os.path.isabs(resolved)


def pack_tree(src, tar_path):
    """Deterministic uncompressed tar of SRC: sorted, owner 0:0, mtime 0, dirs/files/inside-links only."""
    entries = []
    for base, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(dirs + files):
            path = os.path.join(base, name)
            entries.append((os.path.relpath(path, src), path))
    entries.sort()
    total = 0
    with open(tar_path, 'xb') as raw, tarfile.open(fileobj=raw, mode='w', format=tarfile.GNU_FORMAT) as tar:
        for rel, path in entries:
            info = os.lstat(path)
            mode = stat.S_IMODE(info.st_mode)
            if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise Refusal('kernel header entry %s has a setuid/setgid/sticky bit' % rel)
            member = tarfile.TarInfo(rel)
            member.mtime, member.uid, member.gid, member.uname, member.gname = 0, 0, 0, '', ''
            member.mode = 0o755 if stat.S_ISDIR(info.st_mode) or mode & 0o111 else 0o644
            if stat.S_ISDIR(info.st_mode):
                member.type = tarfile.DIRTYPE
                tar.addfile(member)
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if not link_inside(rel, target):
                    raise Refusal('kernel header symlink %s -> %s leaves the tree' % (rel, target))
                member.type, member.linkname, member.mode = tarfile.SYMTYPE, target, 0o777
                tar.addfile(member)
            elif stat.S_ISREG(info.st_mode):
                data = Path(path).read_bytes()
                total += len(data)
                if total > MAX_HEADERS:
                    raise Refusal('kernel header tree exceeds %d bytes' % MAX_HEADERS)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            else:
                raise Refusal('kernel header entry %s is a special file' % rel)


def unpack_tree(tar_path, dest):
    """Extract into the new DEST with the stdlib 'data' filter (no absolute/.. paths, links stay inside)."""
    dest = Path(dest)
    if os.path.lexists(dest):
        raise Refusal('header destination already exists: %s' % dest)
    dest.mkdir(parents=True, mode=0o755)
    with tarfile.open(tar_path, mode='r:') as tar:
        tar.extractall(dest, filter='data')


def kernel_config_sha256(root, release):
    """sha256 of the kernel config, which must be the same bytes in kernel-core and kernel-devel."""
    core = Path(root) / 'usr/lib/modules' / release / 'config'
    devel = Path(root) / 'usr/src/kernels' / release / '.config'
    for path in (core, devel):
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise Refusal('%s is not a regular file' % path)
    if core.read_bytes() != devel.read_bytes():
        raise Refusal('kernel-devel config %s (sha256 %s) differs from kernel-core config %s (sha256 %s)'
                      % (devel, sha256(devel.read_bytes()), core, sha256(core.read_bytes())))
    return sha256(core.read_bytes())


def kernel_export(out, root='/'):
    out = Path(out)
    if os.path.lexists(out):
        raise Refusal('kernel handoff destination already exists: %s' % out)
    release = kernel_release(query_rpm('kernel-core'), query_rpm('kernel-devel'),
                             listing(os.path.join(root, 'usr/src/kernels')), listing(os.path.join(root, 'usr/lib/modules')))
    config = kernel_config_sha256(root, release)
    out.mkdir(parents=True, mode=0o755)
    tar_path = out / 'kernel-headers.tar'
    pack_tree(os.path.join(root, 'usr/src/kernels', release), tar_path)
    facts = {'schemaVersion': 1, 'kernelRelease': release, 'kernelCore': release, 'kernelDevel': release,
             'headersTarSha256': sha256(tar_path.read_bytes()), 'kernelConfigSha256': config}
    (out / 'kernel-facts.json').write_text(json.dumps(facts, indent=2, sort_keys=True) + '\n')
    for path in out.iterdir():
        path.chmod(0o644)
    return facts


def valid_facts(facts):
    release = facts.get('kernelRelease', '')
    if (facts.get('schemaVersion') != 1 or not isinstance(release, str) or not RELEASE.fullmatch(release) or '..' in release
            or facts.get('kernelCore') != release or facts.get('kernelDevel') != release
            or not HEX64.fullmatch(str(facts.get('headersTarSha256', '')))
            or not HEX64.fullmatch(str(facts.get('kernelConfigSha256', '')))):
        raise Refusal('kernel facts are malformed or disagree: %s' % json.dumps(facts, sort_keys=True))
    return facts


def kernel_unpack(handoff, dest):
    handoff = Path(handoff)
    facts = valid_facts(json.loads((handoff / 'kernel-facts.json').read_text()))
    actual = sha256((handoff / 'kernel-headers.tar').read_bytes())
    if actual != facts['headersTarSha256']:
        raise Refusal('kernel header tarball sha256 %s differs from the facts %s' % (actual, facts['headersTarSha256']))
    unpack_tree(handoff / 'kernel-headers.tar', dest)
    return facts


def check_legacy_pairing(pins, rpm):
    """Legacy: the modules must come from the commit of the release the daemon RPM is.

    RPM is None while building (no RPM exists yet: only the pinned identity is checked) or the facts read
    from the downloaded file: name, version, release, sha256 (all must equal the pinned RPM)."""
    legacy = pins['profiles']['legacy']
    daemon = legacy['daemonRpm']
    mapped = LEGACY_RPM_COMMITS.get(daemon['version'])
    if mapped is None:
        raise Refusal('legacy daemon RPM %s has no reviewed opensnitch commit (reviewed: %s); refusing to pair modules with it'
                      % (daemon['version'], ', '.join(sorted(LEGACY_RPM_COMMITS))))
    if legacy['opensnitchCommit'] != mapped:
        raise Refusal('modules and daemon must come from the same opensnitch commit: ebpf %s, daemon RPM %s is %s'
                      % (legacy['opensnitchCommit'], daemon['version'], mapped))
    if legacy['tag'] != 'v' + daemon['version']:
        raise Refusal('legacy source tag %s is not the daemon RPM release v%s' % (legacy['tag'], daemon['version']))
    if rpm is not None:
        for key in ('name', 'version', 'release', 'sha256'):
            if rpm.get(key) != daemon[key]:
                raise Refusal('installed daemon RPM %s %r differs from the pinned %r the modules were built for'
                              % (key, rpm.get(key), daemon[key]))
    return mapped


def check_pairing(pins, profile, daemon_pins=None, native_pins=None, rpm=None):
    """The modules must come from the same opensnitch commit as the daemon that loads them."""
    if profile not in BUILT_PROFILES:
        raise Refusal('profile %s has pinned sources but no eBPF build stage yet' % profile)
    if profile == 'legacy':
        return check_legacy_pairing(pins, rpm)
    if daemon_pins is None or native_pins is None:
        raise Refusal('the system profile pairs with the daemon and snitchwatch pins, which were not given')
    commit = pins['profiles'][profile]['opensnitchCommit']
    if not (commit == daemon_pins['sourceCommit'] == native_pins['submoduleCommit']):
        raise Refusal('modules and daemon must come from the same opensnitch commit: ebpf %s, daemon %s, snitchwatch submodule %s'
                      % (commit, daemon_pins['sourceCommit'], native_pins['submoduleCommit']))
    return commit


def query_rpm_file(path):
    result = subprocess.run(['rpm', '-qp', '--nosignature', '--qf', '%{NAME} %{VERSION} %{RELEASE}\\n', str(path)],
                            capture_output=True, text=True, env=SAFE_ENV)
    return result.returncode, result.stdout


def check_rpm(pins, path):
    """95-firewall.sh, legacy: the downloaded daemon RPM is the pinned one the modules were built for."""
    code, out = query_rpm_file(path)
    fields = out.split()
    if code != 0 or len(fields) != 3:
        raise Refusal('cannot read name, version and release from the daemon RPM %s' % path)
    rpm = {'name': fields[0], 'version': fields[1], 'release': fields[2], 'sha256': sha256(Path(path).read_bytes())}
    check_pairing(pins, 'legacy', rpm=rpm)
    return rpm


def source_pin(pins):
    """Legacy: where prep fetches the module source (repository, commit), after the pairing check."""
    commit = check_pairing(pins, 'legacy')
    return {'repository': pins['profiles']['legacy']['sourceRepository'], 'commit': commit}


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, env={**SAFE_ENV, 'GIT_CONFIG_NOSYSTEM': '1',
                                                                                         'GIT_CONFIG_GLOBAL': '/dev/null'})
    if result.returncode:
        raise Refusal('git %s failed: %s' % (' '.join(args), result.stderr.decode(errors='replace').strip()))
    return result.stdout


def extract_raw(repo, commit, dest):
    """Write ebpf_prog of COMMIT into the new DEST/ebpf_prog from raw blobs (no checkout, filters or attributes)."""
    if git(repo, 'rev-parse', '--verify', '--end-of-options', commit + '^{commit}').decode().strip() != commit:
        raise Refusal('opensnitch checkout does not hold commit %s' % commit)
    dest = Path(dest)
    if os.path.lexists(dest):
        raise Refusal('scratch destination already exists: %s' % dest)
    entries = git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit, '--', 'ebpf_prog').split(b'\0')
    written = 0
    for entry in filter(None, entries):
        meta, _, path = entry.partition(b'\t')
        mode, kind, blob = meta.decode().split()
        rel = path.decode()
        if mode != '100644' or kind != 'blob' or not rel.startswith('ebpf_prog/') or '..' in rel.split('/'):
            raise Refusal('ebpf_prog entry %s is not a plain 100644 blob (mode %s)' % (rel, mode))
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open('xb') as writer:
            writer.write(git(repo, 'cat-file', 'blob', blob))
        target.chmod(0o644)
        written += 1
    if not written:
        raise Refusal('commit %s has no ebpf_prog tree' % commit)
    return dest / 'ebpf_prog'


def make_argv(pins, source, headers, release):
    """The verified invocation: kernel and compiler passed explicitly; EXTRA_FLAGS and ARCH left to the Makefile."""
    cc = ' '.join([pins['tools']['cc']['path'], *pins['ccFlags']])
    return ['make', '-B', '-R', '-r', '-C', str(source), 'KERNEL_VER=' + release[:-len('.x86_64')],
            'KERNEL_DIR=%s' % headers, 'KERNEL_HEADERS=%s' % headers, 'KERNEL_ARCH=x86_64', 'CC=' + cc,
            'LLC=' + pins['tools']['llc']['path']]


def check_tools(pins):
    """Each tool is owned by its pinned RPM and reports the pinned version; returns the clang banner."""
    nevras = {r['name']: r['nevra'] for r in pins['toolchainRpms']}
    banners = {}
    for key, tool in sorted(pins['tools'].items()):
        owner = subprocess.run(['rpm', '-qf', '--qf', '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}', tool['path']],
                               capture_output=True, text=True, env=SAFE_ENV)
        if owner.returncode or owner.stdout != nevras[tool['rpm']]:
            raise Refusal('%s is not owned by the pinned %s: %s' % (tool['path'], nevras[tool['rpm']], owner.stdout + owner.stderr))
        version = subprocess.run([tool['path'], '--version'], capture_output=True, text=True, env=SAFE_ENV)
        lines = version.stdout.splitlines()
        want = 'clang version %s ' % pins['clangVersion'] if key == 'cc' else 'LLVM version %s' % pins['clangVersion']
        if version.returncode or not any(want in line for line in lines):
            raise Refusal('%s does not report %r' % (tool['path'], want.strip()))
        banners[key] = next(line.strip() for line in lines if want in line)
    for name in sorted(nevras):
        verified = subprocess.run(['rpm', '-V', name], capture_output=True, text=True, env=SAFE_ENV)
        if verified.returncode or verified.stdout.strip():
            raise Refusal('installed %s files differ from its RPM (rpm -V): %s' % (name, (verified.stdout + verified.stderr).strip()))
    return banners['cc']


def build(pins, profile, daemon_pins, native_pins, repo, kernel, work, out, rpm_inventory):
    commit = check_pairing(pins, profile, daemon_pins, native_pins)
    expected_tree = pins['profiles'][profile]['ebpfProgTreeSha256']
    for path in (work, out):
        if os.path.lexists(path):
            raise Refusal('build destination already exists: %s' % path)
    clang = check_tools(pins)
    headers = Path(work) / 'kernel-headers'
    facts = kernel_unpack(kernel, headers)
    source = extract_raw(repo, commit, Path(work) / 'build')
    actual_tree = tree_sha256(source)
    if actual_tree != expected_tree:
        raise Refusal('ebpf_prog tree sha256 %s differs from the pin %s' % (actual_tree, expected_tree))
    argv = make_argv(pins, source, headers, facts['kernelRelease'])
    result = subprocess.run(argv, capture_output=True, text=True, env={**SAFE_ENV, 'HOME': str(work)})
    log = '$ %s\n%s%s' % (' '.join(argv), result.stdout, result.stderr)
    if result.returncode:
        sys.stderr.write(log[-4000:])
        raise Refusal('make failed (exit %d)' % result.returncode)
    built = sorted(p.name for p in source.glob('*.o'))
    if built != sorted(pins['objects']):
        raise Refusal('make built %s, want exactly %s' % (built, sorted(pins['objects'])))
    out = Path(out)
    out.mkdir(parents=True, mode=0o755)
    for name in built:
        stripped = subprocess.run([pins['tools']['strip']['path'], '-g', '-o', str(out / name), str(source / name)],
                                  capture_output=True, text=True, env=SAFE_ENV)
        if stripped.returncode:
            raise Refusal('llvm-strip failed for %s: %s' % (name, stripped.stderr.strip()))
    info = {'clang': clang, 'kernelRelease': facts['kernelRelease'], 'make': argv, 'opensnitchCommit': commit,
            'ebpfProgTreeSha256': actual_tree, 'rpmInventorySha256': sha256(Path(rpm_inventory).read_bytes())}
    (out / 'build-info.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    (out / 'kernel-facts.json').write_bytes((Path(kernel) / 'kernel-facts.json').read_bytes())
    (out / 'build.log').write_text(log)
    for path in out.iterdir():
        path.chmod(0o644)
    return info


def stage_overlay(pins_path, profile, daemon_pins, native_pins, handoff, out, kernel_facts):
    """Image overlay: the objects under the staging dir and a manifest built from what this stage can check."""
    raw = Path(pins_path).read_bytes()
    pins = json.loads(raw)
    commit = check_pairing(pins, profile, daemon_pins, native_pins)
    tree = pins['profiles'][profile]['ebpfProgTreeSha256']
    handoff = Path(handoff)
    direct = Path(kernel_facts).read_bytes()
    if direct != (handoff / 'kernel-facts.json').read_bytes():
        raise Refusal('the compile handoff kernel facts differ from the ebpf-kernel stage facts %s' % kernel_facts)
    facts = valid_facts(json.loads(direct))
    info = json.loads((handoff / 'build-info.json').read_text())
    expected = {'kernelRelease': facts['kernelRelease'], 'opensnitchCommit': commit, 'ebpfProgTreeSha256': tree}
    for key, value in expected.items():
        if info.get(key) != value:
            raise Refusal('build-info %s %r differs from %r' % (key, info.get(key), value))
    if not HEX64.fullmatch(str(info.get('rpmInventorySha256', ''))):
        raise Refusal('build-info lacks the compile stage RPM inventory sha256')
    if not str(info.get('clang', '')).startswith('clang version %s ' % pins['clangVersion']):
        raise Refusal('build-info clang %r is not the pinned %s' % (info.get('clang'), pins['clangVersion']))
    out = Path(out)
    if os.path.lexists(out):
        raise Refusal('overlay destination already exists: %s' % out)
    objects_dir = out / STAGED / 'opensnitch-ebpf'
    objects_dir.mkdir(parents=True, mode=0o755)
    for parent in (out / 'usr', out / 'usr/share', out / STAGED):
        parent.chmod(0o755)
    objects = {}
    for name in sorted(pins['objects']):
        data = (handoff / name).read_bytes()
        objects[name] = sha256(data)
        (objects_dir / name).write_bytes(data)
        (objects_dir / name).chmod(0o644)
    # compileRpmInventorySha256 is reported by the compile stage and cannot be independently checked here: informational, not verified provenance.
    manifest = {'schemaVersion': 1, 'profile': profile, 'opensnitchCommit': commit, 'ebpfProgTreeSha256': tree,
                'kernelRelease': facts['kernelRelease'], 'kernelCore': facts['kernelCore'], 'kernelDevel': facts['kernelDevel'],
                'kernelHeadersTarSha256': facts['headersTarSha256'], 'kernelConfigSha256': facts['kernelConfigSha256'],
                'compileRpmInventorySha256': info['rpmInventorySha256'], 'clangVersion': pins['clangVersion'], 'clang': info['clang'],
                'toolchainRpms': sorted(r['nevra'] for r in pins['toolchainRpms']), 'ebpfPinsSha256': sha256(raw),
                'objectsDir': OBJECTS_DIR, 'objects': objects}
    if profile == 'legacy':
        # Recorded so the image can show which RPM the modules were paired with; 95-firewall.sh checks the real file against it.
        manifest['daemonRpm'] = {**pins['profiles']['legacy']['daemonRpm'], 'tag': pins['profiles']['legacy']['tag']}
    (out / STAGED / 'ebpf-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    (out / STAGED / 'ebpf-manifest.json').chmod(0o644)
    return manifest


def load_pins(path):
    raw = Path(path).read_bytes()
    if sha256(raw) != EXPECTED_EBPF_PINS_SHA256:
        raise Refusal('eBPF pins differ from the reviewed digest (update EXPECTED_EBPF_PINS_SHA256 with the pins)')
    return json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('kernel-export')
    export.add_argument('--root', default='/')
    export.add_argument('out', type=Path)
    pins_check = sub.add_parser('check-pins')
    pins_check.add_argument('pins', type=Path)
    rpm_check = sub.add_parser('check-rpm')
    rpm_check.add_argument('--pins', type=Path, required=True)
    rpm_check.add_argument('rpm', type=Path)
    source = sub.add_parser('source-pin')
    source.add_argument('pins', type=Path)
    for name in ('build', 'stage'):
        command = sub.add_parser(name)
        command.add_argument('--pins', type=Path, required=True)
        for flag in ('--daemon-pins', '--native-pins'):  # system only: legacy pairs with the daemon RPM pin instead
            command.add_argument(flag, type=Path)
        command.add_argument('--profile', required=True)
        if name == 'build':
            for flag in ('--repo', '--kernel', '--work', '--out', '--rpm-inventory'):
                command.add_argument(flag, type=Path, required=True)
        else:
            command.add_argument('--kernel-facts', type=Path, required=True)
            command.add_argument('paths', nargs=2, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'kernel-export':
            result = kernel_export(args.out, args.root)
        elif args.command == 'check-pins':
            load_pins(args.pins)
            result = {'pinsSha256': EXPECTED_EBPF_PINS_SHA256}
        elif args.command == 'check-rpm':
            result = check_rpm(load_pins(args.pins), args.rpm)
        elif args.command == 'source-pin':
            result = source_pin(load_pins(args.pins))
        else:
            pins = load_pins(args.pins)
            daemon, native = (json.loads(path.read_text()) if path else None for path in (args.daemon_pins, args.native_pins))
            if args.command == 'build':
                result = build(pins, args.profile, daemon, native, args.repo, args.kernel, args.work, args.out, args.rpm_inventory)
            else:
                result = stage_overlay(args.pins, args.profile, daemon, native, *args.paths, args.kernel_facts)
    except (Refusal, OSError, KeyError, ValueError, tarfile.TarError) as error:
        print('ebpf: %s: %s' % (args.command, error), file=sys.stderr)
        return 1
    print(json.dumps({'ebpf': args.command, **{k: result[k] for k in sorted(result) if k != 'make'}}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
