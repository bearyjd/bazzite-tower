#!/usr/bin/env python3
"""ci/repin-snitchwatch.py: pin arithmetic is computed, never hand-typed.

Everything runs against throwaway git repos in a tempdir: a fake snitchwatch
source repo and a fake tower tree. The real repos are never touched.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'ci/repin-snitchwatch.py'
MANIFEST = 'packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'
OTHER = 'packaging/system/snitchwatch.conf'
FAKE_DAEMON = 'd' * 40
PINS = 'build_files/firewall/snitchwatch-system-pins.json'
VERIFY = 'build_files/firewall/snitchwatch-system-verify.py'
POLICY = '.github/base-refresh-policy.json'
FIX = 'tests/test-snitchwatch-image-build-fixtures/'
OLD = '0' * 40


def manifest(args):
    body = ''.join('  - %s\n' % a for a in args)
    return 'app-id: org.snitchwatch.Snitchwatch\nfinish-args:\n' + body + 'modules:\n  - name: x\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args, env=None, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=check, env=env)


def init(repo):
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 't'); git(repo, 'config', 'user.email', 't@example.invalid')
    git(repo, 'config', 'commit.gpgsign', 'false')


def commit(repo, msg, date='2026-10-25T23:20:26-0400', add=True):
    env = {'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date, 'PATH': '/usr/bin:/bin', 'HOME': str(repo)}
    if add:
        git(repo, 'add', '-A', env=env)
    git(repo, 'commit', '-q', '-m', msg, env=env)
    return git(repo, 'rev-parse', 'HEAD').stdout.strip()


def put(repo, rel, text):
    p = repo / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)


def pins_text(commit_, tree, sub, files, args):
    d = {'bridgeVersion': '0.1.1', 'flatpakFinishArgs': args, 'sourceCommit': commit_,
         'sourceFiles': files, 'sourceTree': tree, 'submoduleCommit': sub}
    return json.dumps(d, indent=2, sort_keys=True) + '\n'


def policy_text(commit_, tree, created, native):
    d = {'schemaVersion': 1, 'sourceCommit': commit_, 'sourceTree': tree, 'sourceCreated': created,
         'nativeSource': native}
    return json.dumps(d, indent=2) + '\n'


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.src = Path(tmp.name) / 'snitchwatch'; self.tower = Path(tmp.name) / 'tower'
        init(self.src)
        self.args = ['--socket=wayland', '--share=ipc', '--device=dri', '--filesystem=xdg-data/icons:ro']
        put(self.src, MANIFEST, manifest(self.args)); put(self.src, OTHER, 'conf v1\n')
        git(self.src, 'add', '-A')
        git(self.src, 'update-index', '--add', '--cacheinfo', '160000,%s,vendor/opensnitch' % FAKE_DAEMON)
        self.sc = commit(self.src, 'v1', add=False)
        self.tree = git(self.src, 'rev-parse', self.sc + '^{tree}').stdout.strip()
        init(self.tower)
        stale = manifest(self.args[:-1])  # fixture/pins lag behind the new manifest
        put(self.tower, FIX + 'org.snitchwatch.Snitchwatch.system.yml', stale)
        put(self.tower, FIX + 'snitchwatch.conf', 'old conf\n')
        put(self.tower, FIX + 'unrelated.txt', 'leave me\n')
        files = {MANIFEST: sha(stale.encode()), OTHER: sha(b'old conf\n')}
        put(self.tower, PINS, pins_text(OLD, '1' * 40, '2' * 40, files, self.args[:-1]))
        put(self.tower, VERIFY, "import sys\nEXPECTED_PINS_SHA256 = '%s'\nBINARY = 'x'\n" % ('9' * 64))
        put(self.tower, POLICY, policy_text(OLD, '3' * 40, '2026-01-01T00:00:00Z',
                                            {'commit': OLD, 'tree': '1' * 40, 'submoduleCommit': '2' * 40}))
        self.base = commit(self.tower, 'base')

    def run_tool(self, *argv, ok=True):
        r = subprocess.run([sys.executable, str(TOOL), '--tower', str(self.tower), *argv], capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def pins(self):
        return json.loads((self.tower / PINS).read_text())

    def snapshot(self):
        return {str(p.relative_to(self.tower)): p.read_bytes() for p in self.tower.rglob('*') if p.is_file() and '.git' not in p.parts}

    def repin(self, *extra):
        return self.run_tool('pins', '--source', str(self.src), '--commit', self.sc, *extra)


class PinsTests(Base):
    def test_dry_run_changes_nothing_and_reports(self):
        before = self.snapshot()
        r = self.repin()
        self.assertEqual(before, self.snapshot())
        self.assertIn(self.sc, r.stdout)

    def test_write_updates_everything(self):
        self.repin('--write')
        p = self.pins()
        self.assertEqual((p['sourceCommit'], p['sourceTree'], p['submoduleCommit']), (self.sc, self.tree, FAKE_DAEMON))
        self.assertEqual(p['sourceFiles'][OTHER], sha(b'conf v1\n'))
        self.assertEqual(p['sourceFiles'][MANIFEST], sha(manifest(self.args).encode()))
        verify = (self.tower / VERIFY).read_text()
        self.assertIn("EXPECTED_PINS_SHA256 = '%s'" % sha((self.tower / PINS).read_bytes()), verify)
        self.assertIn("BINARY = 'x'", verify)
        self.assertEqual((self.tower / FIX / 'snitchwatch.conf').read_text(), 'conf v1\n')
        self.assertEqual((self.tower / FIX / 'org.snitchwatch.Snitchwatch.system.yml').read_text(), manifest(self.args))
        self.assertEqual((self.tower / FIX / 'unrelated.txt').read_text(), 'leave me\n')
        pol = json.loads((self.tower / POLICY).read_text())
        self.assertEqual(pol['nativeSource'], {'commit': self.sc, 'tree': self.tree, 'submoduleCommit': FAKE_DAEMON})
        self.assertEqual(pol['sourceCommit'], OLD)  # tower-side fields belong to the `policy` step

    def test_finish_args_drift_is_corrected_in_order(self):
        self.assertNotEqual(self.pins()['flatpakFinishArgs'], self.args)
        self.repin('--write')
        self.assertEqual(self.pins()['flatpakFinishArgs'], self.args)

    def test_minimal_textual_diff(self):
        self.repin('--write')
        # only the touched lines differ: byte-identical to the canonical layout
        text = (self.tower / PINS).read_text()
        self.assertEqual(text, pins_text(self.sc, self.tree, FAKE_DAEMON, self.pins()['sourceFiles'], self.args))

    def test_idempotent(self):
        self.repin('--write')
        first = self.snapshot()
        self.repin('--write')
        self.assertEqual(first, self.snapshot())
        self.assertEqual(git(self.tower, 'status', '--porcelain').stdout.count('\n'), 5)

    def test_unknown_commit_fails(self):
        r = self.run_tool('pins', '--source', str(self.src), '--commit', 'f' * 40, '--write', ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(git(self.tower, 'status', '--porcelain').stdout, '')


class PolicyTests(Base):
    def land_pins(self):
        self.repin('--write')
        return commit(self.tower, 'repin', date='2026-10-25T23:20:26-0400')

    def test_source_created_is_utc_not_local_wall_time(self):
        sha_ = self.land_pins()
        self.run_tool('policy', '--commit', sha_, '--write')
        pol = json.loads((self.tower / POLICY).read_text())
        # committed 23:20:26 at -0400 == 03:20:26Z the next day
        self.assertEqual(pol['sourceCreated'], '2026-10-26T03:20:26Z')
        self.assertEqual(pol['sourceCommit'], sha_)
        self.assertEqual(pol['sourceTree'], git(self.tower, 'rev-parse', sha_ + '^{tree}').stdout.strip())

    def test_refuses_when_pins_differ_from_native_source(self):
        # tower commit whose pins JSON was not repinned to the policy nativeSource
        pol = json.loads((self.tower / POLICY).read_text()); pol['nativeSource']['commit'] = 'e' * 40
        put(self.tower, POLICY, json.dumps(pol, indent=2) + '\n')
        sha_ = commit(self.tower, 'policy moved, pins did not')
        before = self.snapshot()
        r = self.run_tool('policy', '--commit', sha_, '--write', ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn('nativeSource', r.stderr)
        self.assertEqual(before, self.snapshot())

    def test_refuses_non_ancestor(self):
        sha_ = self.land_pins()
        git(self.tower, 'checkout', '-q', '-b', 'other', self.base)
        r = self.run_tool('policy', '--commit', sha_, '--write', ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn('ancestor', r.stderr)

    def test_dry_run_changes_nothing_and_lists_old_mentions(self):
        sha_ = self.land_pins()
        put(self.tower, 'docs/note.md', 'pinned at %s\n' % OLD)
        sha_ = commit(self.tower, 'doc')
        before = self.snapshot()
        r = self.run_tool('policy', '--commit', sha_)
        self.assertEqual(before, self.snapshot())
        self.assertIn('docs/note.md', r.stdout)

    def test_idempotent(self):
        sha_ = self.land_pins()
        self.run_tool('policy', '--commit', sha_, '--write')
        first = self.snapshot()
        self.run_tool('policy', '--commit', sha_, '--write')
        self.assertEqual(first, self.snapshot())


if __name__ == '__main__':
    unittest.main()
