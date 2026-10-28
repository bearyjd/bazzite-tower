#!/usr/bin/env python3
"""Move the snitchwatch system source pin without hand-computed values.

  repin-snitchwatch.py pins   --source DIR --commit SHA [--write]
  repin-snitchwatch.py policy --commit TOWER_SHA [--write]

Default is a dry run. Edits are targeted text replacements so diffs stay minimal.
Exit 2 means a refused/invalid request (nothing is modified).
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PINS = 'build_files/firewall/snitchwatch-system-pins.json'
VERIFY = 'build_files/firewall/snitchwatch-system-verify.py'
POLICY = '.github/base-refresh-policy.json'
FIXTURES = 'tests/test-snitchwatch-image-build-fixtures'
MANIFEST = 'packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'
GITLINK = 'vendor/opensnitch'
# Crates the scanner build compiles (snitchwatch-system-scanner-build.py PACKAGES); every tracked file is pinned.
SCANNER_CRATES = ('crates/scanner-privileged', 'crates/scanner-broker')


class Refused(Exception):
    pass


def git(repo, *args, raw=False):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if r.returncode:
        raise Refused('git %s failed in %s: %s' % (' '.join(args), repo, r.stderr.decode().strip()))
    return r.stdout if raw else r.stdout.decode().strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sub_once(text, pattern, repl, what):
    new, n = re.subn(pattern, lambda m: m.group(1) + repl + m.group(2), text, flags=re.M)
    if n != 1:
        raise Refused('expected exactly one %s, found %d' % (what, n))
    return new


def set_key(text, key, value, indent='  '):
    return sub_once(text, r'(^%s"%s": ")[^"]*(")' % (indent, key), value, key)


def finish_args(manifest):
    block = re.search(r'^finish-args:\n(.*?)^modules:', manifest, re.M | re.S)
    if not block:
        raise Refused('manifest has no finish-args block')
    for line in re.findall(r'^\s*- .*$', block.group(1), re.M):
        if not re.fullmatch(r'\s+- --\S+\s*', line):
            raise Refused('unsupported finish-args item (only plain "- --xxx" lines are handled): %r' % line.strip())
    return re.findall(r'^\s+- (--\S+)\s*$', block.group(1), re.M)


def set_finish_args(text, args):
    body = ',\n'.join('    ' + json.dumps(a) for a in args)
    return sub_once(text, r'(?s)(^  "flatpakFinishArgs": \[\n).*?(\n  \])', body, 'flatpakFinishArgs')


def set_source_files(text, hashes):
    body = ',\n'.join('    %s: %s' % (json.dumps(p), json.dumps(h)) for p, h in sorted(hashes.items()))
    return sub_once(text, r'(?s)(^  "sourceFiles": \{\n).*?(\n  \})', body, 'sourceFiles')


def set_native(text, commit, tree, sub):
    block = re.search(r'("nativeSource": \{\n)(.*?)(\n  \})', text, re.S)
    if not block:
        raise Refused('policy has no nativeSource block')
    inner = block.group(2)
    for key, val in (('commit', commit), ('tree', tree), ('submoduleCommit', sub)):
        inner = set_key(inner, key, val, '    ')
    return text[:block.start(2)] + inner + text[block.end(2):]


def plan_pins(tower, source, commit):
    commit = git(source, 'rev-parse', '--verify', commit + '^{commit}')
    tree = git(source, 'rev-parse', commit + '^{tree}')
    link = git(source, 'ls-tree', commit, GITLINK).split()
    if len(link) < 3 or link[0] != '160000':
        raise Refused('%s is not a gitlink at %s' % (GITLINK, commit))
    sub = link[2]
    print('target: commit %s tree %s %s %s' % (commit, tree, GITLINK, sub))
    text = (tower / PINS).read_text()
    pins = json.loads(text)
    text = set_key(set_key(set_key(text, 'sourceCommit', commit), 'sourceTree', tree), 'submoduleCommit', sub)
    scanner = git(source, 'ls-tree', '-r', '--name-only', commit, '--', *SCANNER_CRATES).splitlines()
    kept = [p for p in pins['sourceFiles'] if not p.startswith(tuple(c + '/' for c in SCANNER_CRATES))]
    paths = sorted(set(kept) | set(scanner))
    for gone in sorted(set(pins['sourceFiles']) - set(paths)):
        print('dropping %s (no longer in the scanner crates)' % gone)
    blobs = {p: git(source, 'show', '%s:%s' % (commit, p), raw=True) for p in paths}
    text = set_source_files(text, {p: sha256(d) for p, d in blobs.items()})
    if MANIFEST not in blobs:
        raise Refused('%s is not in sourceFiles' % MANIFEST)
    text = set_finish_args(text, finish_args(blobs[MANIFEST].decode()))
    out = {PINS: text.encode()}
    verify = (tower / VERIFY).read_text()
    out[VERIFY] = sub_once(verify, r"(^EXPECTED_PINS_SHA256 = ')[^']*(')", sha256(out[PINS]), 'EXPECTED_PINS_SHA256').encode()
    names = [Path(p).name for p in blobs]
    by_name = {Path(p).name: d for p, d in blobs.items()}
    fixtures = {f.name for f in (tower / FIXTURES).iterdir()}
    for path in blobs:
        name = Path(path).name
        if name not in fixtures:
            if not path.startswith('crates/scanner-'):
                print('no fixture for %s (skipped)' % path)
        elif names.count(name) > 1:
            raise Refused('duplicate basename %s among sourceFiles; cannot match fixture' % name)
        else:
            out['%s/%s' % (FIXTURES, name)] = by_name[name]
    if sub != pins['submoduleCommit']:
        print('WARNING: submoduleCommit changed %s -> %s. policy daemonSource and the daemon pins '
              '(snitchwatch-system-daemon-pins.json, daemon verify) are NOT edited by this tool and need a separate review.'
              % (pins['submoduleCommit'], sub))
    policy = set_native((tower / POLICY).read_text(), commit, tree, sub)
    out[POLICY] = policy.encode()
    return out


def plan_policy(tower, commit):
    commit = git(tower, 'rev-parse', '--verify', commit + '^{commit}')
    if subprocess.run(['git', '-C', str(tower), 'merge-base', '--is-ancestor', commit, 'HEAD']).returncode:
        raise Refused('%s is not an ancestor of HEAD' % commit)
    text = (tower / POLICY).read_text()
    policy = json.loads(text)
    pins = json.loads(git(tower, 'show', '%s:%s' % (commit, PINS)))
    want = {k: pins[v] for k, v in (('commit', 'sourceCommit'), ('tree', 'sourceTree'), ('submoduleCommit', 'submoduleCommit'))}
    if want != policy['nativeSource']:
        raise Refused('pins at %s differ from policy nativeSource: %s != %s' % (commit[:12], want, policy['nativeSource']))
    verify = git(tower, 'show', '%s:%s' % (commit, VERIFY))
    expected = re.search(r"^EXPECTED_PINS_SHA256 = '([^']*)'", verify, re.M)
    pins_bytes = git(tower, 'show', '%s:%s' % (commit, PINS), raw=True)
    if not expected or expected.group(1) != sha256(pins_bytes):
        raise Refused('EXPECTED_PINS_SHA256 at %s does not match the pins file there (half-landed repin?)' % commit[:12])
    tree = git(tower, 'rev-parse', commit + '^{tree}')
    stamp = datetime.datetime.fromtimestamp(int(git(tower, 'log', '-1', '--format=%ct', commit)), datetime.timezone.utc)
    text = set_key(set_key(set_key(text, 'sourceCommit', commit), 'sourceTree', tree),
                   'sourceCreated', stamp.strftime('%Y-%m-%dT%H:%M:%SZ'))
    return {POLICY: text.encode()}, policy['sourceCommit'], commit


def replace_file(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.repin-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply(tower, plan, write):
    for rel, data in plan.items():
        path = tower / rel
        if path.read_bytes() == data:
            continue
        print(('wrote  ' if write else 'would change  ') + rel)
        if write:
            replace_file(path, data)
    if not write:
        print('dry run: nothing modified (use --write)')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tower', type=Path, default=ROOT, help='tower tree (default: this repo)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pins'); p.add_argument('--source', type=Path, required=True)
    q = sub.add_parser('policy')
    for s in (p, q):
        s.add_argument('--commit', required=True); s.add_argument('--write', action='store_true')
    a = ap.parse_args(argv)
    try:
        if a.commit.startswith('-'):
            raise Refused('--commit value must not start with "-"')
        if a.cmd == 'pins':
            plan = plan_pins(a.tower, a.source, a.commit)
        else:
            plan, old, new = plan_policy(a.tower, a.commit)
        apply(a.tower, plan, a.write)
        if a.cmd == 'policy':
            if old == new:
                print('sourceCommit unchanged; skipping stale-mention scan')
            else:
                hits = subprocess.run(['git', '-C', str(a.tower), 'grep', '-lIF', old],
                                      capture_output=True, text=True).stdout.split()
                print('files still mentioning previous sourceCommit %s (review by hand):' % old[:12])
                print('\n'.join('  ' + h for h in hits) or '  (none)')
    except Refused as e:
        print('error: %s' % e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
