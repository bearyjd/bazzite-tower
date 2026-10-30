#!/usr/bin/env python3
"""Compose only verified native binary/licenses and pinned system overlay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import stat
import subprocess
import tempfile
import time


# Opt-in Vigie endpoint-event feed: pristine pinned upstream copies, shipped
# inert under /usr/share (no unit search path, nothing enabled).  The verifier
# pins the same names; the operator installs them by hand (docs/RUNBOOK.md).
ENDPOINT_EVENTS = ('snitchwatch-endpoint-events.conf', 'snitchwatch-endpoint-events.nft',
                   'snitchwatch-endpoint-events.service', 'snitchwatch-endpoint-events-nft.service')
# The bridge binary is built by ~660 third-party crates, so it never runs as
# root here: its --version check and upstream stage.sh (which runs its --help)
# run as this uid/gid with an empty environment, then root adopts only these
# paths (H1).  Contents are pinned by the verifier (SOURCE_FILES, binary hash).
UNPRIVILEGED = 65534
# What `reproducible` in native-build-provenance.json rests on (M1 steps 2-3).
REPRODUCIBLE_BASIS = ('byte-identical sibling build: the assembly stage compared the snitchwatch-bridge and '
                      'snitchwatch-bridge-rebuild tarballs, built from the same prep layer on the same runner, '
                      'both offline as uid 65534; not an independent host or toolchain. reproLogSha256 is the '
                      'first stage\'s own repro-check.sh log, which that stage could forge.')
UNPRIVILEGED_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
SYSTEM_STAGE_FILES = {
    'usr/bin/snitchwatch-bridge-cli': 0o755,
    'usr/lib/systemd/system/snitchwatch-system-bridge.service': 0o644,
    'usr/lib/systemd/system/snitchwatch-system-bridge-grpc.socket': 0o644,
    'usr/lib/systemd/system/snitchwatch-system-bridge-gui.socket': 0o644,
    'usr/lib/systemd/system/opensnitch.service.d/snitchwatch-system-bridge.conf': 0o644,
    'etc/opensnitchd/default-config.json': 0o644,
    'usr/lib/tmpfiles.d/snitchwatch.conf': 0o644,
    'usr/lib/sysusers.d/snitchwatch.conf': 0o644,
    'etc/opensnitchd/rules/000-snitchwatch-bridge-fetch.json': 0o644,
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


PR_SET_CHILD_SUBREAPER = 36
PR_SET_NO_NEW_PRIVS = 38
# upstream stage.sh only hashes, runs --help and installs nine files.
STAGE_SH_TIMEOUT = 300
_LIBC = []


def libc():
    if not _LIBC:
        import ctypes
        _LIBC.append(ctypes.CDLL(None, use_errno=True))
    return _LIBC[0]


def prctl(option, value):
    """libc prctl(2); unprivileged_options() loads libc in the parent so the forked child only calls it."""
    return libc().prctl(option, value, 0, 0, 0)


def set_no_new_privs():
    """preexec_fn: runs in the child after the uid/gid drop and before exec; a failure aborts the exec."""
    if prctl(PR_SET_NO_NEW_PRIVS, 1) != 0:
        raise OSError('prctl(PR_SET_NO_NEW_PRIVS, 1) failed')


def unprivileged_options():
    """subprocess options that drop root to UNPRIVILEGED with no_new_privs (no setuid/file-cap gain on exec)."""
    libc()
    options = {'env': dict(UNPRIVILEGED_ENV), 'cwd': '/', 'stdin': subprocess.DEVNULL, 'umask': 0o022,
               'preexec_fn': set_no_new_privs}
    if os.geteuid() == 0:
        options.update(user=UNPRIVILEGED, group=UNPRIVILEGED, extra_groups=[])
    return options


def become_subreaper():
    """Orphans of the bridge binary (double fork, setsid) reparent to this process, not to init."""
    import ctypes
    if prctl(PR_SET_CHILD_SUBREAPER, 1) != 0:
        raise ValueError('cannot become child subreaper to contain the bridge binary: errno %d' % ctypes.get_errno())


def children(parent, proc=Path('/proc')):
    """PIDs whose parent is PARENT (/proc/PID/stat field 4, after the parenthesised comm)."""
    found = []
    for entry in proc.iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            except (FileNotFoundError, ProcessLookupError, IndexError):
                continue
            if int(fields[1]) == parent:
                found.append(int(entry.name))
    return found


def kill_leftover_children(proc=Path('/proc'), rounds=50):
    """SIGKILL and reap every remaining descendant (all reparented here); refuse if any survive.

    Only this process's own children are touched, never unrelated processes that a
    user namespace may also show as uid 65534."""
    me = os.getpid()
    for _ in range(rounds):
        left = children(me, proc)
        if not left:
            return
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in left:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        time.sleep(0.05)
    raise ValueError('bridge binary descendants survived SIGKILL; refusing')


def run_unprivileged(command, **extra):
    privileged = os.geteuid() == 0
    if privileged:
        become_subreaper()
    try:
        return subprocess.run(command, check=True, **unprivileged_options(), **extra)
    except PermissionError as error:
        raise ValueError('cannot drop to uid/gid %d to run the bridge binary: %s' % (UNPRIVILEGED, error)) from error
    except subprocess.TimeoutExpired as error:
        name = Path(command[1] if command[0] == 'bash' else command[0]).name
        # The configured limit, never subprocess's measured remaining time (a float).
        raise ValueError('%s timed out after %d s running the bridge binary' % (name, extra['timeout'])) from error
    except subprocess.CalledProcessError:
        raise
    except subprocess.SubprocessError as error:
        # The only preexec_fn is set_no_new_privs: fail closed, never exec without it.
        raise ValueError('cannot set no_new_privs for the bridge binary (preexec failed): %s' % error) from error
    finally:
        if privileged:
            kill_leftover_children()


def require_not_writable_by(path, uid):
    """The executable and every parent must be unwritable by uid (sticky world-writable dirs excepted).

    uid None (a non-root caller, which runs the binary as itself) checks modes only."""
    for current in (path, *path.parents):
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('bridge executable path contains a symlink: ' + str(current))
        sticky_dir = stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX and current != path
        if (uid is not None and info.st_uid == uid) or (info.st_mode & 0o022 and not sticky_dir):
            raise ValueError('bridge executable or a parent is writable by uid %s or by group/other: %s' % (uid, current))


def adopt_unprivileged_stage(staged, out):
    """Copy the unprivileged stage.sh output into the new OUT as the caller (root in the build).

    Only SYSTEM_STAGE_FILES may appear, as regular single-link files with their
    exact mode; directories only as their parents.  Presence and content are the
    verifier's job (REQUIRED, SOURCE_FILES, binary hash), as before."""
    parents = {str(parent) for name in SYSTEM_STAGE_FILES for parent in Path(name).parents if str(parent) != '.'}
    if os.path.lexists(staged) and not stat.S_ISDIR(os.lstat(staged).st_mode):
        raise ValueError('unexpected path in unprivileged stage.sh output: the staging root is not a real directory')
    out.mkdir()
    for base, subdirs, files in os.walk(staged):
        subdirs.sort()
        for name in sorted(subdirs) + sorted(files):
            origin = Path(base) / name
            rel = str(origin.relative_to(staged))
            info = os.lstat(origin)
            if stat.S_ISDIR(info.st_mode) and rel in parents:
                (out / rel).mkdir(exist_ok=True)
                (out / rel).chmod(0o755)
            elif stat.S_ISREG(info.st_mode) and SYSTEM_STAGE_FILES.get(rel) == stat.S_IMODE(info.st_mode) and info.st_nlink == 1:
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                with origin.open('rb') as reader, (out / rel).open('xb') as writer:
                    writer.write(reader.read())
                (out / rel).chmod(SYSTEM_STAGE_FILES[rel])
            else:
                raise ValueError('unexpected path in unprivileged stage.sh output: %s (mode %o)' % (rel, info.st_mode))


def stage_system_unprivileged(source, out, binary, binary_sha):
    """Run pinned upstream stage.sh (it executes BINARY --help) unprivileged into a scratch root."""
    scratch = Path(tempfile.mkdtemp(prefix='snitchwatch-bridge-stage.'))
    try:
        if os.geteuid() == 0:
            os.chown(scratch, UNPRIVILEGED, UNPRIVILEGED)
        run_unprivileged(['bash', str(source / 'packaging/system/stage.sh'), str(scratch / 'root'), str(binary), binary_sha],
                         timeout=STAGE_SH_TIMEOUT)
        adopt_unprivileged_stage(scratch / 'root', out)
    finally:
        shutil.rmtree(scratch)


def copy(source, dest, mode=0o644):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def require_exact_clean_source(source, pins):
    git = lambda *values: subprocess.check_output(['git', '-C', str(source), *values], text=True).strip()
    head, tree, dirty = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}'), git('status', '--porcelain', '--ignored')
    if head != pins['sourceCommit'] or tree != pins['sourceTree'] or dirty:
        raise ValueError('source checkout is not exact and completely clean: HEAD %s (want %s), tree %s (want %s), status:\n%s' % (
            head, pins['sourceCommit'], tree, pins['sourceTree'], '\n'.join(dirty.splitlines()[:20])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('source', 'artifact', 'out', 'pins', 'verifier', 'legacy-config', 'candidate-config', 'build-log', 'repro-log', 'rpm-inventory', 'tarball', 'rebuild-tarball', 'os-update-rule', 'resolved-dns-rule', 'scanner-artifact'):
        parser.add_argument('--' + arg, required=True, type=Path)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out == Path('/') or out.exists():
        raise ValueError('output must be a new isolated staging directory')
    pins = json.loads(args.pins.read_text())
    require_exact_clean_source(source, pins)
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
    binary = args.artifact.resolve() / 'usr/bin/snitchwatch-bridge-cli'
    require_not_writable_by(binary, UNPRIVILEGED if os.geteuid() == 0 else None)
    binary_sha = sha(binary)
    actual_version = run_unprivileged([str(binary), '--version'], text=True, timeout=5, capture_output=True).stdout.strip()
    if actual_version != 'snitchwatch-bridge-cli ' + pins['bridgeVersion']:
        raise ValueError('actual binary --version differs from required bridge version')
    if sha(binary) != binary_sha:
        raise ValueError('bridge executable changed while it ran')
    # Stage from this exact source checkout, never install the schema1 user unit.
    stage_system_unprivileged(source, out, binary, binary_sha)
    if sha(out / 'usr/bin/snitchwatch-bridge-cli') != binary_sha:
        raise ValueError('staged bridge executable differs from the verified artifact')
    # stage.sh also stages upstream's deny/ebpf daemon config. The tower keeps
    # its reviewed allow/proc baseline; eBPF requires owner testing. Drop it: a file
    # pre-placed at that path would meet the RPM's cpio extraction (mtime-
    # dependent overwrite, see 95-firewall.sh), and 95-firewall.sh installs the
    # reviewed tower candidate (allow + proc) there after extraction anyway.
    staged_config = out / 'etc/opensnitchd/default-config.json'
    if sha(staged_config) != pins['sourceFiles']['packaging/system/opensnitchd-default-config.json']:
        raise ValueError('stage.sh did not stage the pinned upstream daemon config')
    staged_config.unlink()
    scanner = args.scanner_artifact
    scanner_provenance = json.loads((scanner / 'provenance.json').read_text())
    if (scanner_provenance.get('sourceCommit') != pins['sourceCommit']
            or scanner_provenance.get('sourceTree') != pins['sourceTree']
            or scanner_provenance.get('reproducible') is not True
            or scanner_provenance.get('testsPassed') is not True):
        raise ValueError('scanner source/test/repro identity mismatch')
    binaries = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
    for name in binaries:
        if sha(scanner / name) != scanner_provenance['binaries'][name]:
            raise ValueError('scanner executable differs from build receipt')
    subprocess.run(['bash', str(source / 'packaging/scanner/stage.sh'), str(out),
                    str(scanner / binaries[0]), scanner_provenance['binaries'][binaries[0]],
                    str(scanner / binaries[1]), scanner_provenance['binaries'][binaries[1]]], check=True)
    for name, expected in scanner_provenance['evidence'].items():
        if name not in {'source-inputs.json', 'dependency-closure.json', 'THIRD-PARTY-LICENSES.md', 'LICENSE', 'build.log', 'repro.log', 'test.log', *(binary + '.ldd' for binary in binaries)}:
            raise ValueError('unexpected scanner evidence path')
        if sha(scanner / name) != expected:
            raise ValueError('scanner evidence hash mismatch: ' + name)
        destination = ('usr/share/licenses/snitchwatch-scanner/' if name in {'LICENSE', 'THIRD-PARTY-LICENSES.md'}
                       else 'usr/share/snitchwatch/scanner/')
        copy(scanner / name, out / destination / name)
    copy(scanner / 'provenance.json', out / 'usr/share/snitchwatch/scanner/provenance.json')
    for name in ENDPOINT_EVENTS:
        pinned = 'packaging/endpoint-events/' + name
        if sha(source / pinned) != pins['sourceFiles'][pinned]:
            raise ValueError('endpoint-events file differs from pin: ' + pinned)
        copy(source / pinned, out / 'usr/share/snitchwatch/endpoint-events' / name)
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
    # M1 step 2: evidence from this stage's own comparison with the sibling
    # build, not a literal (assemble.sh already refused a mismatch; this repeats it).
    reproducible = (not os.path.samefile(args.tarball, args.rebuild_tarball)
                    and args.tarball.read_bytes() == args.rebuild_tarball.read_bytes())
    if not reproducible:
        raise ValueError('bridge rebuild is not a separate byte-identical tarball')
    provenance = {
        'sourceCommit': pins['sourceCommit'], 'submoduleCommit': pins['submoduleCommit'],
        'sourceTree': pins['sourceTree'],
        'builderImage': pins['builderImage'], 'artifactSha256': sha(args.tarball),
        'rpmInventorySha256': sha(args.rpm_inventory), 'reproducible': reproducible,
        'buildLogSha256': sha(args.build_log), 'reproLogSha256': sha(args.repro_log),
        'reproCommand': 'snitchwatch-system-handoff.py compare-bridge <snitchwatch-bridge tarball> <snitchwatch-bridge-rebuild tarball>',
        'reproducibleBasis': REPRODUCIBLE_BASIS,
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
    validator['verify'](out, image_tree=True)
    print(json.dumps({'stagingRoot': str(out), 'manifestSha256': sha(p), 'binarySha256': binary_sha}, sort_keys=True))


if __name__ == '__main__':
    main()
