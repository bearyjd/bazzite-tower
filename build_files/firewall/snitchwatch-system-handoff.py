#!/usr/bin/env python3
"""Stage-boundary guards for the split snitchwatch-system build (finding H1).

Subcommands (each failure prints one distinct `handoff:` line and exits 1):
  probe-network online|offline  prove the RUN does (prep) or does not (scanner,
                                assembly) reach the network; a real connect attempt
  export KIND --pins P SRC DEST copy only KIND's allowlisted files, fixed modes
  check KIND --pins P ROOT      the receiving side: exact allowlist, regular files,
                                fixed modes, owner, no links/special files/set-id/caps
  merge SRC DEST                add a checked overlay to DEST, refusing any overlap

COPY --from is not a trust boundary by itself; `check` is.  KINDs: scanner,
bridge, daemon (the daemon allowlist is the trusted verifier's REQUIRED set).
"""
import argparse
import errno
import json
import os
from pathlib import Path
import runpy
import socket
import stat
import sys

SCANNER_BINARIES = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
SCANNER_FILES = ('provenance.json', 'source-inputs.json', 'dependency-closure.json', 'THIRD-PARTY-LICENSES.md',
                 'LICENSE', 'build.log', 'repro.log', 'test.log', *(name + '.ldd' for name in SCANNER_BINARIES))
BRIDGE_ARCH = 'x86_64'
REFUSED_XATTRS = ('security.capability', 'system.posix_acl_access', 'system.posix_acl_default')
# Literal addresses need no resolver; the name needs DNS.  Only a completed TCP
# connect counts as reaching the network: a name can resolve locally (nss,
# /etc/hosts, a host resolver socket) without any route.  Any connect while
# offline, or none while online, is a refusal.
PROBE_ADDRESSES = (('1.1.1.1', 443), ('2606:4700:4700::1111', 443))
PROBE_NAME = ('registry.fedoraproject.org', 443)
PROBE_TIMEOUT = 5


class Refusal(Exception):
    pass


def bridge_tarball(pins):
    return 'snitchwatch-bridge-%s-%s.tar.gz' % (pins['bridgeVersion'], BRIDGE_ARCH)


def allowlist(kind, pins=None, daemon_verifier=None):
    """{relative path: exact mode} of everything KIND may hand over; nothing else."""
    if kind == 'scanner':
        return {**{name: 0o755 for name in SCANNER_BINARIES}, **{name: 0o644 for name in SCANNER_FILES}}
    if kind == 'bridge':
        tarball = bridge_tarball(pins)
        return {tarball: 0o644, tarball + '.sha256': 0o644, 'build.log': 0o644, 'repro.log': 0o644}
    if kind == 'daemon':
        if daemon_verifier is None:
            raise Refusal('daemon allowlist needs the trusted daemon verifier')
        verifier = runpy.run_path(str(daemon_verifier))
        executable = {verifier['CANDIDATE'], verifier['VERIFIER']}
        names = set(verifier['REQUIRED']) | {verifier['MANIFEST']}
        return {name.lstrip('/'): 0o755 if name in executable else 0o644 for name in names}
    raise Refusal('unknown handoff kind: ' + kind)


def ancestors(names):
    return {str(parent) for name in names for parent in Path(name).parents if str(parent) != '.'}


def require_no_refused_xattrs(path, rel):
    try:
        present = os.listxattr(path, follow_symlinks=False)
    except OSError as error:
        # Only "this filesystem has no xattrs" is an answer; any other error fails closed.
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return
        raise Refusal('cannot list extended attributes of %s: %s' % (rel, error)) from error
    for name in present:
        if name in REFUSED_XATTRS:
            raise Refusal('%s carries refused extended attribute %s' % (rel, name))


def check(kind, root, pins=None, daemon_verifier=None, owner=(0, 0)):
    """Refuse anything in ROOT that is not exactly KIND's allowlist."""
    expected = allowlist(kind, pins, daemon_verifier)
    dirs = ancestors(expected)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode):
        raise Refusal('%s handoff root is not a real directory: %s' % (kind, root))
    entries = [(root, '.', info)]
    found = set()
    for base, subdirs, files in os.walk(root):
        for name in sorted(subdirs + files):
            path = os.path.join(base, name)
            entries.append((path, os.path.relpath(path, root), os.lstat(path)))
    for path, rel, info in entries:
        mode = stat.S_IMODE(info.st_mode)
        if (info.st_uid, info.st_gid) != tuple(owner):
            raise Refusal('%s handoff entry %s is owned by %d:%d, want %d:%d' % (kind, rel, info.st_uid, info.st_gid, *owner))
        if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise Refusal('%s handoff entry %s has a setuid/setgid/sticky bit (mode %o)' % (kind, rel, mode))
        require_no_refused_xattrs(path, rel)
        if stat.S_ISDIR(info.st_mode):
            if rel != '.' and rel not in dirs:
                raise Refusal('unexpected directory in %s handoff: %s' % (kind, rel))
            if mode & 0o022:
                raise Refusal('%s handoff directory %s is group/other writable (mode %o)' % (kind, rel, mode))
        elif stat.S_ISREG(info.st_mode):
            if rel not in expected:
                raise Refusal('unexpected file in %s handoff: %s' % (kind, rel))
            if mode != expected[rel]:
                raise Refusal('%s handoff file %s has mode %o, want %o' % (kind, rel, mode, expected[rel]))
            if info.st_nlink != 1:
                raise Refusal('%s handoff file %s is hard-linked (%d links)' % (kind, rel, info.st_nlink))
            found.add(rel)
        else:
            raise Refusal('%s handoff entry %s is a symlink or special file' % (kind, rel))
    missing = sorted(set(expected) - found)
    if missing:
        raise Refusal('%s handoff is missing: %s' % (kind, ', '.join(missing)))
    return sorted(found)


def export(kind, source, dest, pins=None, daemon_verifier=None):
    """Copy only the allowlisted regular files from SOURCE into the new DEST."""
    expected = allowlist(kind, pins, daemon_verifier)
    if os.path.lexists(dest):
        raise Refusal('%s handoff destination already exists: %s' % (kind, dest))
    dest.mkdir(parents=True)
    dest.chmod(0o755)
    for rel, mode in sorted(expected.items()):
        origin = source / rel
        if not stat.S_ISREG(os.lstat(origin).st_mode):
            raise Refusal('%s export source %s is not a regular file' % (kind, rel))
        target = dest / rel
        for parent in reversed(Path(rel).parents[:-1]):
            (dest / parent).mkdir(exist_ok=True)
            (dest / parent).chmod(0o755)
        with origin.open('rb') as reader, target.open('xb') as writer:
            writer.write(reader.read())
        target.chmod(mode)
    return sorted(expected)


def merge(source, dest):
    """Copy the checked overlay SOURCE into DEST; any existing non-directory path is an overlap."""
    if not stat.S_ISDIR(os.lstat(dest).st_mode):
        raise Refusal('merge destination is not a real directory: %s' % dest)
    added = []
    for base, subdirs, files in os.walk(source):
        subdirs.sort()
        rel_base = os.path.relpath(base, source)
        target_base = dest if rel_base == '.' else dest / rel_base
        if os.path.lexists(target_base) and not stat.S_ISDIR(os.lstat(target_base).st_mode):
            raise Refusal('merge overlap: %s exists in the destination and is not a directory' % rel_base)
        if not os.path.lexists(target_base):
            target_base.mkdir()
            target_base.chmod(0o755)
        for name in sorted(files):
            rel = os.path.normpath(os.path.join(rel_base, name))
            target = dest / rel
            if os.path.lexists(target):
                raise Refusal('merge overlap: %s already exists in the destination' % rel)
            origin = source / rel
            with origin.open('rb') as reader, target.open('xb') as writer:
                writer.write(reader.read())
            target.chmod(stat.S_IMODE(os.lstat(origin).st_mode))
            added.append(rel)
    return added


def interfaces(dev='/proc/net/dev'):
    """Interfaces of this process's network namespace (/proc/net follows the netns; /sys may not)."""
    lines = Path(dev).read_text().splitlines()[2:]
    return sorted(line.split(':', 1)[0].strip() for line in lines if ':' in line)


def attempts(connect=socket.create_connection, resolve=socket.getaddrinfo):
    """[(target, None or error text)] for every probe target; None means it connected."""
    results = []
    for host, port in PROBE_ADDRESSES:
        results.append(('%s:%d' % (host, port), try_connect(connect, host, port)))
    host, port = PROBE_NAME
    try:
        resolved = resolve(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        results.append(('dns ' + host, 'resolution failed: %s' % error))
    else:
        results.append(('dns ' + host, 'resolved (not a reachability signal)'))
        family_host = resolved[0][4][0]
        results.append(('%s:%d (%s)' % (host, port, family_host), try_connect(connect, family_host, port)))
    return results


def try_connect(connect, host, port):
    try:
        connection = connect((host, port), timeout=PROBE_TIMEOUT)
    except OSError as error:
        return '%s: %s' % (type(error).__name__, error)
    connection.close()
    return None


def probe_network(expect, dev='/proc/net/dev', connect=socket.create_connection, resolve=socket.getaddrinfo):
    names = interfaces(dev)
    results = attempts(connect, resolve)
    reached = [target for target, error in results if error is None]
    if expect == 'offline':
        if names != ['lo']:
            raise Refusal('network probe: interfaces %s present, want only lo; RUN --network=none is not in effect' % names)
        if reached:
            raise Refusal('network probe: outbound connection succeeded to %s; RUN --network=none is not enforced' % ', '.join(reached))
    elif expect == 'online':
        if not reached:
            raise Refusal('network probe positive control failed: no target reachable from a networked stage (%s); '
                          'an offline result elsewhere would prove nothing' % '; '.join('%s %s' % pair for pair in results))
    else:
        raise Refusal('probe expectation must be online or offline')
    return {'expect': expect, 'interfaces': names, 'results': {target: error or 'connected' for target, error in results}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    probe = sub.add_parser('probe-network')
    probe.add_argument('expect', choices=('online', 'offline'))
    for name in ('export', 'check'):
        command = sub.add_parser(name)
        command.add_argument('kind', choices=('scanner', 'bridge', 'daemon'))
        command.add_argument('--pins', type=Path)
        command.add_argument('--daemon-verifier', type=Path)
        command.add_argument('paths', nargs=2 if name == 'export' else 1, type=Path)
    merging = sub.add_parser('merge')
    merging.add_argument('paths', nargs=2, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'probe-network':
            result = probe_network(args.expect)
        elif args.command == 'merge':
            result = {'merged': len(merge(*args.paths))}
        else:
            pins = json.loads(args.pins.read_text()) if args.pins else None
            if args.kind == 'bridge' and pins is None:
                raise Refusal('bridge handoff needs --pins')
            if args.command == 'export':
                result = {'exported': export(args.kind, args.paths[0], args.paths[1], pins, args.daemon_verifier)}
            else:
                result = {'checked': len(check(args.kind, args.paths[0], pins, args.daemon_verifier))}
    except (Refusal, OSError, KeyError, ValueError) as error:
        print('handoff: %s: %s' % (args.command, error), file=sys.stderr)
        return 1
    print(json.dumps({'handoff': args.command, **result}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
