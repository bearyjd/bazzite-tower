#!/usr/bin/env python3
"""M1 step 3: run the bridge build's third-party code as an unprivileged uid.

Used by both sibling bridge stages (snitchwatch-system-bridge-build.sh and
-bridge-rebuild.sh, profile `bridge`) and by the scanner stage
(snitchwatch-system-scanner-stage.sh, profile `scanner`). PROFILE is optional and
defaults to bridge. Each failure prints one distinct `unpriv:` line and exits 1.

  prepare WORK [PROFILE]
                       root, before any third-party code: refuse unless the
                       profile's read-only trees (the source checkout; for the
                       scanner also its export) are root-owned with no group/other
                       write and other-readable, nothing on the filesystem is owned by
                       BUILD_UID/BUILD_GID, then create the empty scratch dirs
                       and hand them (and the allowlisted cargo home) to BUILD_UID
  run WORK -- CMD...   root: fork; in the child drop the bounding set, ambient and
                       inheritable capabilities, all groups, then gid and uid, set
                       no_new_privs, and refuse to exec unless /proc/self/status
                       shows exactly that; afterwards SIGKILL every leftover
                       descendant (this process is their subreaper) and refuse
                       if any survives. Only descendants: in a user namespace,
                       unrelated processes can also show as uid 65534
  selfcheck WORK [PROFILE]
                       as BUILD_UID (through `run`): print uid/caps/no_new_privs,
                       prove deliberate writes to root-owned tooling fail, the
                       scratch dirs are writable, and nothing else on the
                       filesystem is writable or owned by BUILD_UID

uid 65534: the uid stage.py already drops to in this builder (CI-proven setuid
and chown there), with no passwd entry or useradd needed. Adding a package or
user to prep would change the rpm -qa set that buildinfo embeds in the tarball.
"""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

BUILD_UID = BUILD_GID = 65534
# Writable by BUILD_UID, all directly under WORK. bridge-cargo is the
# Cargo.lock-allowlisted home made by root just before; the rest start empty.
SCRATCH = ('bridge-cargo', 'dist', 'target', 'home')
FRESH = ('dist', 'target', 'home')
# The usual container PATH (every dir root-owned, checked); /usr/local holds nothing here.
PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
# Loader/interpreter injection variables never cross the drop (none is set by
# the stage scripts; everything else is inherited unchanged for byte equality).
DROPPED_ENV = ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT', 'GCONV_PATH', 'PYTHONPATH', 'PYTHONHOME',
               'PYTHONSTARTUP', 'BASH_ENV', 'ENV', 'CDPATH')
SKIP_TREES = ('/proc', '/sys', '/dev')
STATUS_FIELDS = ('Uid', 'Gid', 'Groups', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs')
# Deliberate writes that must fail as BUILD_UID (the loud demo; the filesystem walk in
# selfcheck is the general proof). WORK and FACTORY are substituted.
PROBES = (*(('create', d) for d in ('/usr/bin', '/usr/local/bin', '/usr/lib/sysimage/rpm', 'FACTORY', 'WORK',
                                     'WORK/source', 'WORK/source/.git', 'WORK/cargo', 'WORK/toolchain')),
          ('file', '/etc/ld.so.preload'),
          *(('append', f) for f in ('/usr/bin/cc', '/usr/bin/gcc', '/usr/bin/ld', '/usr/bin/rustc', '/usr/bin/cargo',
                                    '/usr/bin/python3', 'WORK/source/Cargo.lock', 'WORK/rpm-inventory.txt',
                                    'FACTORY/snitchwatch-system-unpriv.py')))
# Per stage: scratch (writable by BUILD_UID, directly under WORK; those not in fresh are
# the cargo home root made just before), read-only trees prepare checks, and extra probes.
PROFILES = {
    'bridge': {'scratch': SCRATCH, 'fresh': FRESH, 'readonly': ('source',), 'probes': ()},
    'scanner': {'scratch': ('scanner-cargo', 'scanner', 'home'), 'fresh': ('scanner', 'home'),
                'readonly': ('source', 'scanner-inputs'),
                'probes': (('create', 'WORK/scanner-inputs'), ('create', 'WORK/scanner-inputs/source'),
                           *(('append', 'WORK/scanner-inputs/' + f) for f in ('source/Cargo.lock', 'source-inputs.json',
                                                                              'build-inputs.json')),
                           ('append', 'FACTORY/snitchwatch-system-scanner-build.py')),
                # Read by the build phase (provenance, pin arguments): fail early if the mode or a parent blocks BUILD_UID.
                'readable': ('WORK/rpm-inventory.txt', 'FACTORY/snitchwatch-system-pins.json')},
}
READONLY_LABEL = {'source': 'source checkout', 'scanner-inputs': 'scanner input tree'}
DENIED = (13, 1, 30)  # EACCES, EPERM, EROFS
PR_CAPBSET_DROP = 24
PR_SET_CHILD_SUBREAPER = 36
PR_SET_NO_NEW_PRIVS = 38
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
LINUX_CAPABILITY_VERSION_3 = 0x20080522
_LIBC = []


class Refusal(Exception):
    pass


def libc():
    if not _LIBC:
        import ctypes
        _LIBC.append(ctypes.CDLL(None, use_errno=True))
    return _LIBC[0]


def prctl(option, arg=0):
    return libc().prctl(option, arg, 0, 0, 0)


def clear_inheritable():
    """capset(2) with inheritable 0, effective/permitted unchanged (root may lower them)."""
    import ctypes

    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]

    header, data = Header(LINUX_CAPABILITY_VERSION_3, 0), (Data * 2)()
    if libc().capget(ctypes.byref(header), data) != 0:
        return -1
    for half in data:
        half.inheritable = 0
    return libc().capset(ctypes.byref(header), data)


def child_env(environ, work):
    env = {name: value for name, value in environ.items() if name not in DROPPED_ENV}
    env.update(PATH=PATH, HOME=str(Path(work) / 'home'))
    return env


def parse_status(text):
    fields = {}
    for line in text.splitlines():
        name, _, value = line.partition(':')
        if name in STATUS_FIELDS:
            fields[name] = value.split()
    return fields


def status_problems(fields, uid=BUILD_UID, gid=BUILD_GID):
    """Everything in /proc/self/status that is not exactly the dropped state."""
    want = {'Uid': [str(uid)] * 4, 'Gid': [str(gid)] * 4, 'Groups': [], 'NoNewPrivs': ['1'],
            **{name: ['0' * 16] for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')}}
    return ['%s is %s, want %s' % (name, ' '.join(fields.get(name, ['<missing>'])) or '<empty>', ' '.join(value) or '<empty>')
            for name, value in want.items() if fields.get(name) != value]


def last_cap(path='/proc/sys/kernel/cap_last_cap'):
    return int(Path(path).read_text())


def drop_privileges(uid=BUILD_UID, gid=BUILD_GID, caps=None, call=prctl, inheritable=clear_inheritable, system=os,
                    status='/proc/self/status', warn=lambda text: os.write(2, text.encode())):
    """preexec_fn: runs in the forked child as root; any failure aborts the exec (fail closed)."""
    caps = last_cap() if caps is None else caps
    bounding = int(parse_status(Path(status).read_text()).get('CapBnd', ['f' * 16])[0], 16)
    for cap in range(caps + 1):
        if bounding >> cap & 1 and call(PR_CAPBSET_DROP, cap) != 0:
            raise OSError('cannot drop capability %d from the bounding set' % cap)
    if call(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL) != 0:
        raise OSError('cannot clear the ambient capability set')
    if inheritable() != 0:
        raise OSError('cannot clear the inheritable capability set')
    system.setgroups([])
    system.setresgid(gid, gid, gid)
    system.setresuid(uid, uid, uid)
    if call(PR_SET_NO_NEW_PRIVS, 1) != 0:
        raise OSError('cannot set no_new_privs')
    problems = status_problems(parse_status(Path(status).read_text()), uid, gid)
    if problems:
        warn('unpriv: run: refusing to exec, the drop did not take: %s\n' % '; '.join(problems))
        raise OSError('privilege drop not in effect')


def children(parent, proc=Path('/proc')):
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


def kill_leftovers(proc=Path('/proc'), rounds=50, pause=0.05, me=None):
    """SIGKILL and reap every descendant that outlived the command (all reparented here)."""
    me = os.getpid() if me is None else me
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
        time.sleep(pause)
    raise Refusal('descendants of the unprivileged build survived SIGKILL')


def run(work, command, popen=subprocess.Popen, euid=os.geteuid, subreaper=lambda: prctl(PR_SET_CHILD_SUBREAPER, 1),
        reap=kill_leftovers):
    if euid() != 0:
        raise Refusal('run must start as root (it is the privilege drop)')
    if not command:
        raise Refusal('run needs a command after --')
    libc()
    if subreaper() != 0:
        raise Refusal('cannot become child subreaper')
    try:
        child = popen(command, env=child_env(os.environ, work), stdin=subprocess.DEVNULL, umask=0o022,
                      preexec_fn=drop_privileges, close_fds=True)
    except subprocess.SubprocessError as error:
        raise Refusal('the privilege drop failed before exec; nothing ran (%s)' % error) from error
    try:
        return child.wait()
    finally:
        reap()


def walk(top):
    """(path, lstat) of every entry under TOP on its filesystem, outside SKIP_TREES; unlistable dirs listed apart."""
    entries, unlistable = [], []
    device = os.lstat(top).st_dev
    for base, subdirs, files in os.walk(top, onerror=lambda error: unlistable.append(error.filename)):
        kept = []
        for name in sorted(subdirs):
            path = os.path.join(base, name)
            info = os.lstat(path)
            entries.append((path, info))
            if path not in SKIP_TREES and info.st_dev == device and not stat.S_ISLNK(info.st_mode):
                kept.append(name)
        subdirs[:] = kept
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                entries.append((path, os.lstat(path)))
            except FileNotFoundError:
                continue
    return entries, unlistable


def owned_by_build_ids(top='/', uid=BUILD_UID, gid=BUILD_GID):
    """Entries of TOP's own filesystem owned by uid or gid. Mount points of other filesystems
    (/proc, /sys, /dev, bind mounts) are left out: in a user namespace their unmapped owner
    shows as 65534 without being it (the selfcheck's access test still covers them)."""
    device = os.lstat(top).st_dev
    return [path for path, info in walk(top)[0] if info.st_dev == device and (info.st_uid == uid or info.st_gid == gid)]


def source_problems(source, owner=(0, 0)):
    problems = []
    for path, info in [(str(source), os.lstat(source)), *walk(str(source))[0]]:
        mode = stat.S_IMODE(info.st_mode)
        if (info.st_uid, info.st_gid) != tuple(owner):
            problems.append('%s is owned by %d:%d' % (path, info.st_uid, info.st_gid))
        elif stat.S_ISLNK(info.st_mode):
            continue
        elif mode & 0o022:
            problems.append('%s is group/other writable (mode %o)' % (path, mode))
        elif not mode & 0o004 or (stat.S_ISDIR(info.st_mode) and not mode & 0o001):
            problems.append('%s is not other-readable (mode %o)' % (path, mode))
    return problems


def profile_of(name):
    if name not in PROFILES:
        raise Refusal('unknown profile: %r' % name)
    return PROFILES[name]


def prepare(work, top='/', uid=BUILD_UID, gid=BUILD_GID, owner=(0, 0), chown=os.lchown, profile='bridge'):
    work, chosen = Path(work), profile_of(profile)
    for path in (work.parent, work):
        info = os.lstat(path)
        if (info.st_uid, info.st_gid) != tuple(owner) or stat.S_IMODE(info.st_mode) & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise Refusal('%s must be a root-owned directory without group/other write' % path)
    for tree in chosen['readonly']:
        problems = source_problems(work / tree, owner)
        if problems:
            raise Refusal('%s is not root-owned read-only for uid %d: %s' % (READONLY_LABEL[tree], uid, '; '.join(problems[:20])))
    owned = owned_by_build_ids(top, uid, gid)
    if owned:
        raise Refusal('paths already owned by uid/gid %d before the build: %s' % (uid, ', '.join(owned[:20])))
    for name in chosen['scratch']:
        cargo = work / name
        if name not in chosen['fresh'] and not (os.path.lexists(cargo) and stat.S_ISDIR(os.lstat(cargo).st_mode)):
            raise Refusal('the allowlisted cargo home is not a directory: %s' % cargo)
    for name in chosen['fresh']:
        if os.path.lexists(work / name):
            raise Refusal('scratch directory already exists: %s' % (work / name))
        (work / name).mkdir(mode=0o755)
    for name in chosen['scratch']:
        for path, info in [(str(work / name), os.lstat(work / name)), *walk(str(work / name))[0]]:
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Refusal('scratch entry is not a regular file or directory: %s' % path)
            chown(path, uid, gid)
    return {'buildUid': uid, 'profile': profile, 'scratch': [str(work / name) for name in chosen['scratch']]}


def probe(kind, path):
    """None if a write as this uid is refused (EACCES/EPERM/EROFS), 'absent' if there is
    nothing to write to, else what went wrong. Never truncates or writes data.

    create: a new file inside the directory PATH; file: create PATH itself, or open it
    for appending if it exists; append: open the existing file PATH for appending."""
    if kind == 'file' and os.path.lexists(path):
        kind = 'append'
    if not os.path.lexists(os.path.dirname(path) if kind == 'file' else path):
        return 'absent'
    try:
        if kind == 'append':
            os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
            return 'opened for writing: ' + path
        target = path if kind == 'file' else os.path.join(path, '.unpriv-write-probe')
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        os.unlink(target)
        return 'created ' + target
    except OSError as error:
        return None if error.errno in DENIED else '%s: %s' % (path, error)


def unreadable(path):
    """None if this uid can open PATH for reading and read a byte, else a clear message."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        return ('input is not readable by uid %d: %s (%s); it must be other-readable in a traversable directory'
                % (os.getuid(), path, error.strerror))
    try:
        os.read(descriptor, 1)
    except OSError as error:
        return 'input cannot be read by uid %d: %s (%s)' % (os.getuid(), path, error.strerror)
    finally:
        os.close(descriptor)
    return None


def writable_outside(top, allowed, uid):
    """Paths under TOP this uid could write (or owns) other than ALLOWED trees and sticky dirs."""
    entries, unlistable = walk(top)
    device = os.lstat(top).st_dev
    found = []
    for path, info in entries:
        if any(path == root or path.startswith(root + '/') for root in allowed) or stat.S_ISLNK(info.st_mode):
            continue
        sticky = stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
        owned = info.st_dev == device and info.st_uid == uid  # see owned_by_build_ids
        if owned or (not sticky and os.access(path, os.W_OK, follow_symlinks=False)):
            found.append(path)
    return found, unlistable


def selfcheck(work, factory, top='/', uid=BUILD_UID, gid=BUILD_GID, status='/proc/self/status', probes=None, path=PATH,
              profile='bridge'):
    work, factory, chosen = str(work), str(factory), profile_of(profile)
    fields = parse_status(Path(status).read_text())
    report = {'uid': os.getuid(), 'euid': os.geteuid(), 'status': {k: ' '.join(v) for k, v in fields.items()},
              'path': os.environ.get('PATH'), 'home': os.environ.get('HOME'), 'profile': profile}
    problems = status_problems(fields, uid, gid)
    sub = lambda p: p.replace('WORK', work, 1).replace('FACTORY', factory, 1)
    probes = probes or [(kind, sub(target)) for kind, target in (*PROBES, *chosen['probes'])]
    for kind, target in probes:
        result = probe(kind, target)
        report.setdefault('absentProbes' if result == 'absent' else 'deniedWrites', []).append(target)
        if result and result != 'absent':
            problems.append('write to a root-owned path did not fail: ' + result)
    for target in chosen.get('readable', ()):
        problem = unreadable(sub(target))
        if problem:
            problems.append(problem)
    scratch = [os.path.join(work, name) for name in chosen['scratch']]
    for directory in scratch:
        if not (probe('create', directory) or '').startswith('created '):
            problems.append('scratch dir is not writable: ' + directory)
    for entry in path.split(':'):
        if os.path.isdir(entry) and os.access(entry, os.W_OK):
            problems.append('PATH dir is writable: ' + entry)
    writable, unlistable = writable_outside(top, scratch, uid)
    if writable:
        problems.append('writable or owned outside the scratch dirs: ' + ', '.join(writable[:20]))
    report['unlistableDirs'] = unlistable[:20]
    print(json.dumps({'unpriv': 'selfcheck', **report}, sort_keys=True))
    if problems:
        raise Refusal('; '.join(problems))
    return report


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    factory = Path(__file__).resolve().parent
    command = argv[0] if argv else ''
    try:
        profile = argv[2] if len(argv) == 3 else 'bridge'
        if command == 'prepare' and len(argv) in (2, 3) and profile in PROFILES:
            print(json.dumps({'unpriv': 'prepare', **prepare(argv[1], profile=profile)}, sort_keys=True))
            return 0
        if command == 'run' and len(argv) >= 4 and argv[2] == '--':
            return run(argv[1], argv[3:])
        if command == 'selfcheck' and len(argv) in (2, 3) and profile in PROFILES:
            selfcheck(argv[1], factory, profile=profile)
            return 0
        raise Refusal('usage: prepare WORK [bridge|scanner] | run WORK -- CMD... | selfcheck WORK [bridge|scanner]')
    except (Refusal, OSError, ValueError) as error:
        print('unpriv: %s: %s' % (command or '-', error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
