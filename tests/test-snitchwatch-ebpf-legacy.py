#!/usr/bin/env python3
"""Offline tests for the legacy-profile eBPF module build (v1.8.0 pairing).

Split from test-snitchwatch-ebpf.py (size); its fixtures and helpers are
imported, not copied. Covers the legacy pins and pairing, the pinned daemon RPM
(identity, query format, equality checks in the image helper and smoke), the
prep source-pin handling, the assembly output shape and the wiring. No
container, network or compiler.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

_spec = importlib.util.spec_from_file_location('ebpf_tests', Path(__file__).with_name('test-snitchwatch-ebpf.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
ROOT, FACTORY, EBPF, EBPF_PINS_PATH, CHECK = base.ROOT, base.FACTORY, base.EBPF, base.EBPF_PINS_PATH, base.CHECK
DAEMON_PINS, BPF_HEADER, R72, R73 = base.DAEMON_PINS, base.BPF_HEADER, base.R72, base.R73
LEGACY_RPM, LEGACY_PIN, load = base.LEGACY_RPM, base.LEGACY_PIN, base.load


class LegacyPins(unittest.TestCase):
    def setUp(self):
        self.raw = EBPF_PINS_PATH.read_bytes()
        self.pins = json.loads(self.raw)
        self.e = load(EBPF)

    def legacy_pins(self, **changes):
        pins = json.loads(self.raw)
        legacy = pins['profiles']['legacy']
        rpm_changes = changes.pop('daemonRpm', {})
        legacy.update(changes)
        legacy['daemonRpm'] = {**legacy['daemonRpm'], **rpm_changes}
        return pins

    def test_legacy_pairs_with_the_v180_rpm_daemon_and_needs_no_system_pins(self):
        legacy = self.pins['profiles']['legacy']
        self.assertEqual(self.e['check_pairing'](self.pins, 'legacy'), 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')
        self.assertEqual(self.e['LEGACY_RPM_COMMITS'], {'1.8.0': legacy['opensnitchCommit']})
        self.assertEqual(legacy['tag'], 'v1.8.0')
        self.assertEqual({k: legacy['daemonRpm'][k] for k in ('name', 'version', 'release')},
                         {'name': 'opensnitch', 'version': '1.8.0', 'release': '1'})
        self.assertEqual(legacy['sourceRepository'], DAEMON_PINS['sourceRepository'])

    def test_legacy_refuses_an_rpm_version_with_no_reviewed_commit(self):
        # An RPM bump must fail loudly: even with the tag edited to match, the reviewed table has no commit for it.
        pins = self.legacy_pins(tag='v1.9.0', daemonRpm={'version': '1.9.0'})
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['check_pairing'](pins, 'legacy')
        self.assertIn('no reviewed opensnitch commit', str(caught.exception))

    def test_legacy_refuses_modules_from_a_different_commit(self):
        for commit in (DAEMON_PINS['sourceCommit'], 'f' * 40):
            with self.subTest(commit):
                with self.assertRaises(self.e['Refusal']) as caught:
                    self.e['check_pairing'](self.legacy_pins(opensnitchCommit=commit), 'legacy')
                self.assertIn('same opensnitch commit', str(caught.exception))

    def test_legacy_refuses_a_source_tag_that_is_not_the_rpm_release(self):
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['check_pairing'](self.legacy_pins(tag='v1.9.0'), 'legacy')
        self.assertIn('source tag', str(caught.exception))

    def test_legacy_refuses_an_installed_rpm_that_is_not_the_pinned_one(self):
        good = {'name': 'opensnitch', 'version': '1.8.0', 'release': '1', 'sha256': self.pins['profiles']['legacy']['daemonRpm']['sha256']}
        self.assertEqual(self.e['check_pairing'](self.pins, 'legacy', rpm=good), 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')
        for key, value in (('version', '1.9.0'), ('release', '2'), ('name', 'opensnitch-ui'), ('sha256', 'f' * 64)):
            with self.subTest(key):
                with self.assertRaises(self.e['Refusal']) as caught:
                    self.e['check_pairing'](self.pins, 'legacy', rpm={**good, key: value})
                self.assertIn(key, str(caught.exception))

    def test_source_pin_names_the_one_commit_to_fetch(self):
        self.assertEqual(self.e['source_pin'](self.pins), {
            'repository': 'https://github.com/evilsocket/opensnitch.git', 'commit': 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc'})
        result = subprocess.run(['python3', '-B', str(EBPF), 'source-pin', str(EBPF_PINS_PATH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['commit'], 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')

    def test_check_rpm_accepts_only_the_pinned_rpm_file(self):
        data = b'rpm bytes'
        pins = self.legacy_pins(daemonRpm={'sha256': hashlib.sha256(data).hexdigest()})
        with tempfile.TemporaryDirectory(prefix='sw-rpm.') as tmp:
            rpm = Path(tmp) / 'opensnitch-1.8.0-1.x86_64.rpm'
            rpm.write_bytes(data)
            globals_ = self.e['check_rpm'].__globals__
            with mock.patch.dict(globals_, {'query_rpm_file': lambda path: (0, 'opensnitch 1.8.0 1\n')}):
                self.assertEqual(self.e['check_rpm'](pins, rpm)['version'], '1.8.0')
            for answer, needle in (((0, 'opensnitch 1.9.0 1\n'), 'version'), ((0, 'opensnitch 1.8.0 2\n'), 'release'),
                                   ((1, ''), 'cannot read'), ((0, 'garbage\n'), 'cannot read')):
                with self.subTest(answer):
                    with mock.patch.dict(globals_, {'query_rpm_file': lambda path, answer=answer: answer}):
                        with self.assertRaises(self.e['Refusal']) as caught:
                            self.e['check_rpm'](pins, rpm)
                    self.assertIn(needle, str(caught.exception))
            rpm.write_bytes(b'a different rpm')
            with mock.patch.dict(globals_, {'query_rpm_file': lambda path: (0, 'opensnitch 1.8.0 1\n')}):
                with self.assertRaises(self.e['Refusal']) as caught:
                    self.e['check_rpm'](pins, rpm)
            self.assertIn('sha256', str(caught.exception))


class LegacyImageCheck(base.Workdir):
    image = base.ImageCheck.image
    run_check = base.ImageCheck.run_check

    def setUp(self):
        super().setUp()
        self.c = load(CHECK)

    def test_legacy_image_with_a_matching_v180_manifest_passes(self):
        result = self.run_check(self.image(profile='legacy'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('legacy', result.stdout)
        self.assertIn(R72, result.stdout)
        self.assertIn('v1.8.0', result.stdout)

    def test_legacy_manifest_must_be_for_the_v180_commit(self):
        for commit in ('a' * 40, 'a1353848ba1b660320e90cefea782c3fba272c00'):
            result = self.run_check(self.image(profile='legacy', commit=commit))
            self.assertNotEqual(result.returncode, 0, commit)
            self.assertIn('v1.8.0 commit', result.stderr)

    def test_legacy_manifest_must_record_the_v180_rpm(self):
        for rpm in (None, {**LEGACY_RPM, 'version': '1.9.0'}, {**LEGACY_RPM, 'name': 'opensnitch-ui'}, {**LEGACY_RPM, 'tag': 'v1.9.0'},
                    {**LEGACY_RPM, 'sha256': 'xyz'}, 'not a dict'):
            with self.subTest(rpm):
                result = self.run_check(self.image(profile='legacy', rpm=rpm))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('daemon RPM', result.stderr)

    def test_legacy_still_checks_kernel_config_and_object_hashes(self):
        self.assertNotEqual(self.run_check(self.image(profile='legacy', kernel=R73)).returncode, 0)
        root = self.image(profile='legacy')
        (root / 'usr/lib/opensnitchd/ebpf/opensnitch-dns.o').write_bytes(BPF_HEADER + b'rpm copy')
        result = self.run_check(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('opensnitch-dns.o', result.stderr)


class LegacyWiring(unittest.TestCase):
    def firewall_text(self):
        return (ROOT / 'build_files/build.d/95-firewall.sh').read_text()

    def test_firewall_step_refuses_a_legacy_rpm_that_is_not_the_pinned_one_before_extracting_it(self):
        text = self.firewall_text()
        check = text.index('snitchwatch-system-ebpf.py" check-rpm')
        self.assertLess(text.index('sha256sum -c -'), check)
        self.assertLess(check, text.index('rpm2cpio "/tmp'))
        gate = [line for line in text[:check].splitlines() if line.lstrip().startswith('if ')][-1]
        self.assertIn('== legacy', gate)
        self.assertIn('"/tmp/${opensnitch_rpm}"', text[check:check + 400])

    def test_firewall_rpm_constants_equal_the_pinned_legacy_daemon_rpm(self):
        # A half-landed RPM bump (95-firewall.sh moved, pins not) goes red here.
        text = self.firewall_text()
        version = re.search(r'^\s*opensnitch_version="([^"]+)"', text, re.M).group(1)
        sha = re.search(r'^\s*opensnitch_sha256="([0-9a-f]{64})"', text, re.M).group(1)
        rpm = json.loads(EBPF_PINS_PATH.read_text())['profiles']['legacy']['daemonRpm']
        self.assertEqual((version, sha), (rpm['version'], rpm['sha256']))
        self.assertIn('opensnitch_rpm="opensnitch-${opensnitch_version}-%s.x86_64.rpm"' % rpm['release'], text)
        self.assertEqual(rpm['file'], 'opensnitch-%s-%s.x86_64.rpm' % (rpm['version'], rpm['release']))

    def test_pinned_legacy_identity_is_the_same_in_pins_check_helper_and_smoke(self):
        legacy = json.loads(EBPF_PINS_PATH.read_text())['profiles']['legacy']
        check = load(CHECK)
        self.assertEqual(check['LEGACY_OPENSNITCH_COMMIT'], legacy['opensnitchCommit'])
        self.assertEqual(check['LEGACY_RPM_VERSION'], legacy['daemonRpm']['version'])
        smoke = (ROOT / 'tests/smoke.sh').read_text()
        legacy_branch = smoke[smoke.index('\nlegacy)\n'):smoke.index('\nsystem)\n')]
        self.assertIn(legacy['opensnitchCommit'], legacy_branch)
        self.assertIn('.daemonRpm.version == "%s"' % legacy['daemonRpm']['version'], legacy_branch)
        self.assertIn('/usr/share/bazzite-tower/ebpf-manifest.json', legacy_branch)
        self.assertNotIn('check "legacy image has no eBPF build manifest"', legacy_branch)
        self.assertEqual(self.e_commits(), {legacy['daemonRpm']['version']: legacy['opensnitchCommit']})

    def e_commits(self):
        return load(EBPF)['LEGACY_RPM_COMMITS']


class LegacyReviewFixes(base.Workdir):
    def setUp(self):
        super().setUp()
        self.c = load(CHECK)

    def test_pins_carry_no_unverified_tag_object(self):
        # tagObject was dead data (nothing verified it); the commit is the pin.
        self.assertNotIn('tagObject', LEGACY_PIN)
        self.assertNotIn('tagObject', EBPF_PINS_PATH.read_text())

    def test_query_rpm_file_asks_for_exactly_the_three_fields_check_rpm_parses(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout='opensnitch 1.8.0 1\n')

        with mock.patch.dict(self.e['query_rpm_file'].__globals__, {'subprocess': mock.Mock(run=fake_run)}):
            self.assertEqual(self.e['query_rpm_file']('/x.rpm'), (0, 'opensnitch 1.8.0 1\n'))
        self.assertEqual(calls, [['rpm', '-qp', '--nosignature', '--qf', '%{NAME} %{VERSION} %{RELEASE}\\n', '/x.rpm']])

    def test_image_helper_compares_rpm_sha_release_and_tree_for_equality(self):
        check = load(CHECK)
        self.assertEqual(check['LEGACY_RPM_SHA256'], LEGACY_PIN['daemonRpm']['sha256'])
        self.assertEqual(check['LEGACY_RPM_RELEASE'], LEGACY_PIN['daemonRpm']['release'])
        self.assertEqual(check['LEGACY_TREE_SHA256'], LEGACY_PIN['ebpfProgTreeSha256'])
        image = base.ImageCheck.image
        for rpm in ({**LEGACY_RPM, 'sha256': 'f' * 64}, {**LEGACY_RPM, 'release': '2'}):
            with self.subTest(rpm):
                result = base.ImageCheck.run_check(self, image(self, profile='legacy', rpm=rpm))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('daemon RPM', result.stderr)

    def test_image_helper_refuses_a_legacy_tree_that_is_not_the_pinned_one(self):
        root = base.ImageCheck.image(self, profile='legacy')
        manifest = root / 'usr/share/bazzite-tower/ebpf-manifest.json'
        data = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**data, 'ebpfProgTreeSha256': 'b' * 64}))
        result = base.ImageCheck.run_check(self, root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ebpf_prog', result.stderr)

    def test_helper_documents_that_it_does_not_verify_the_rpm(self):
        text = CHECK.read_text()
        start = text.index('def verify_legacy_pairing')
        doc = text[start:text.index('\n\n\n', start)]
        self.assertIn('does NOT verify the RPM', doc)

    def test_smoke_asserts_the_pinned_rpm_sha_release_and_tree_by_equality(self):
        smoke = (ROOT / 'tests/smoke.sh').read_text()
        branch = smoke[smoke.index('\nlegacy)\n'):smoke.index('\nsystem)\n')]
        rpm = LEGACY_PIN['daemonRpm']
        self.assertIn('.daemonRpm.sha256 == "%s"' % rpm['sha256'], branch)
        self.assertIn('.daemonRpm.release == "%s"' % rpm['release'], branch)
        self.assertIn('.ebpfProgTreeSha256 == "%s"' % LEGACY_PIN['ebpfProgTreeSha256'], branch)

    def test_this_file_runs_in_both_build_workflow_blocks_and_is_listed_for_agents(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-snitchwatch-ebpf-legacy.py'), 2)
        self.assertIn('python3 tests/test-snitchwatch-ebpf-legacy.py', (ROOT / 'AGENTS.md').read_text())

    def assemble_final_check(self):
        text = (FACTORY / 'snitchwatch-system-ebpf-legacy-assemble.sh').read_text()
        marker = '# Final output shape'
        self.assertEqual(text.count(marker), 1)
        self.assertLess(text.index('snitchwatch-system-legacy-marker'), text.index(marker))
        return text[text.index(marker):]

    def run_final_check(self, out):
        return subprocess.run(['bash', '-c', 'set -euo pipefail\nout=$1\n' + self.assemble_final_check(), 'x', str(out)],
                              capture_output=True, text=True)

    def legacy_output(self, name):
        out = self.root / name
        share = out / 'usr/share/bazzite-tower'
        (share / 'opensnitch-ebpf').mkdir(parents=True)
        for name in ('opensnitch.o', 'opensnitch-procs.o', 'opensnitch-dns.o'):
            (share / 'opensnitch-ebpf' / name).write_bytes(b'o')
        (share / 'ebpf-manifest.json').write_text('{}')
        (share / 'snitchwatch-bridge-profile').write_text('legacy\n')
        return out

    def test_assembly_refuses_any_output_that_is_not_exactly_the_marker_objects_and_manifest(self):
        out = self.legacy_output('ok')
        ok = self.run_final_check(out)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        share = out / 'usr/share/bazzite-tower'

        def extra_file():
            (share / 'extra').write_text('x')

        def extra_dir():
            (out / 'etc').mkdir()

        def symlink_object():
            (share / 'opensnitch-ebpf/opensnitch.o').unlink()
            (share / 'opensnitch-ebpf/opensnitch.o').symlink_to('/etc/passwd')

        def missing_object():
            (share / 'opensnitch-ebpf/opensnitch-dns.o').unlink()

        def symlink_dir():
            (out / 'usr/lib').symlink_to('share')

        for mutate in (extra_file, extra_dir, symlink_object, missing_object, symlink_dir):
            with self.subTest(mutate.__name__):
                out = self.legacy_output(mutate.__name__)
                share = out / 'usr/share/bazzite-tower'
                mutate()
                refused = self.run_final_check(out)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('ebpf legacy assembly', refused.stderr)


class LegacyBuilderTools(base.Workdir):
    """The legacy builder is bare fedora:45 + python3 (ebpf-legacy-base) + the prep stage's installs.

    First CI run failed on `cmp: command not found` (diffutils is not in the bare image). Probed on the
    pinned image: cmp, diff, git, make and python3 are absent; everything else below ships in it.
    Probe (podman run, bash --noprofile --norc, command -v) covered every name in PROVIDER.
    """
    # Every external command the legacy chain's scripts and helpers use, and what provides it.
    # 'bare' = in the pinned fedora:45 image as probed; otherwise the package that must be installed.
    PROVIDER = {
        'bash': 'bare', 'timeout': 'bare', 'dnf': 'bare', 'rpm': 'bare', 'sort': 'bare', 'stat': 'bare', 'find': 'bare',
        'install': 'bare', 'sha256sum': 'bare', 'cut': 'bare', 'tail': 'bare', 'mktemp': 'bare', 'mkdir': 'bare',
        'rm': 'bare', 'dirname': 'bare', 'curl': 'bare', 'python3': 'python3', 'cmp': 'diffutils', 'git': 'git-core', 'make': 'make',
    }
    # Names a script could plausibly shell out to; a hit that PROVIDER does not list is unaudited.
    VOCABULARY = ('cmp', 'diff', 'git', 'make', 'curl', 'tar', 'cpio', 'rpm2cpio', 'patch', 'sed', 'awk', 'grep', 'xxd', 'which',
                  'unzip', 'gzip', 'xz', 'jq', 'wget', 'gcc', 'ld', 'readlink', 'realpath', 'dirname', 'basename', 'tee',
                  'sha256sum', 'sort', 'stat', 'find', 'install', 'timeout', 'mktemp', 'cut', 'tail', 'head', 'tr', 'cp',
                  'mv', 'ln', 'chmod', 'chown', 'uname', 'rpm', 'dnf', 'python3')
    SCRIPTS = ('snitchwatch-system-ebpf-legacy-base.sh', 'snitchwatch-system-ebpf-prep.sh', 'snitchwatch-system-ebpf-build.sh',
               'snitchwatch-system-ebpf-legacy-assemble.sh', 'snitchwatch-system-toolchain.sh', 'snitchwatch-system-mode.sh')

    def code(self, name):
        text = (FACTORY / name).read_text()
        text = re.sub(r'(?m)^\s*#.*$', '', text)
        return re.sub(r"<<'PY'.*?\nPY\n", '', text, flags=re.S)

    def used(self, name):
        text = self.code(name)
        found = {c for c in self.VOCABULARY if re.search(r'(?<![\w./$-])%s(?![\w.-])' % re.escape(c), text)}
        # Python helpers shell out through subprocess; make is the one non-literal argv[0].
        return found

    def installed(self):
        base_text = self.code('snitchwatch-system-ebpf-legacy-base.sh')
        installed = set(re.findall(r'install_weak_deps=False install ([\w-]+)\s*$', base_text, flags=re.M))
        prep = (FACTORY / 'snitchwatch-system-ebpf-prep.sh').read_text()
        extras = re.search(r'if \[\[ "\$profile" == legacy \]\]; then\n(?:\s*#.*\n)*\s*command -v curl.*\n\s*extras=\(([^)]*)\)', prep)
        self.assertIsNotNone(extras, 'legacy extras array not found in the prep script')
        return installed | set(extras.group(1).split())

    def test_every_command_the_legacy_chain_uses_is_audited_and_provided(self):
        installed = self.installed()
        self.assertIn('python3', installed)
        for name in self.SCRIPTS:
            for cmd in self.used(name):
                with self.subTest(script=name, command=cmd):
                    self.assertIn(cmd, self.PROVIDER, 'unaudited command %s in %s' % (cmd, name))
                    provider = self.PROVIDER[cmd]
                    self.assertTrue(provider == 'bare' or provider in installed,
                                    '%s (%s) is not in the bare builder image nor installed by the legacy stages' % (cmd, name))
        for helper in ('snitchwatch-system-ebpf.py', 'snitchwatch-system-handoff.py'):
            text = (FACTORY / helper).read_text()
            for cmd in set(re.findall(r"subprocess\.run\(\[\s*'([\w-]+)'", text)) | ({'make'} if "['make'," in text else set()):
                with self.subTest(helper=helper, command=cmd):
                    self.assertIn(cmd, self.PROVIDER)
                    self.assertTrue(self.PROVIDER[cmd] == 'bare' or self.PROVIDER[cmd] in installed)

    def test_cmp_needs_diffutils_in_the_legacy_stages(self):
        # Mutation target: dropping diffutils from the legacy extras must turn this red.
        self.assertIn('diffutils', self.installed())
        self.assertIn('cmp', self.used('snitchwatch-system-ebpf-build.sh'))

    def inventory_check(self):
        text = (FACTORY / 'snitchwatch-system-ebpf-build.sh').read_text()
        start = text.index("command -v cmp")
        return text[start:text.index('ebpf_pins=')]

    def run_inventory_check(self, bins, live, recorded):
        work = self.root / 'work'
        work.mkdir()
        (work / 'rpm-inventory.txt').write_text(recorded)
        tools = self.root / 'bin'
        tools.mkdir()
        rpm = tools / 'rpm'
        rpm.write_text('#!/bin/bash\nprintf %s "$LIVE_INVENTORY"\n')
        rpm.chmod(0o755)
        for name in bins:
            (tools / name).symlink_to(subprocess.run(['bash', '-c', 'command -v ' + name], capture_output=True, text=True).stdout.strip())
        return subprocess.run(['/usr/bin/bash', '--noprofile', '--norc', '-c', 'set -euo pipefail\nwork=$1\n' + self.inventory_check() + 'echo OK', 'x', str(work)],
                              capture_output=True, text=True, env={'PATH': str(tools), 'LIVE_INVENTORY': live})

    def test_inventory_check_reports_a_missing_cmp_distinctly_and_never_as_a_difference(self):
        result = self.run_inventory_check(['sort', 'bash'], 'a\n', 'a\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cmp (diffutils) is missing', result.stderr)
        self.assertNotIn('RPM set differs', result.stderr)
        self.assertNotIn('command not found', result.stderr)

    def test_inventory_check_still_refuses_any_difference_and_accepts_equal_sets(self):
        tools = ['sort', 'cmp', 'bash']
        for live in ('a\nb\n', 'b\n', ''):
            with self.subTest(live=live):
                self.setUp()
                refused = self.run_inventory_check(tools, live, 'a\n')
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('RPM set differs', refused.stderr)
        self.setUp()
        ok = self.run_inventory_check(tools, 'a\n', 'a\n')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('OK', ok.stdout)

    def test_no_legacy_script_compares_with_a_bare_cmp_pipeline(self):
        # A `| cmp - file ||` pattern cannot tell a missing cmp from a difference.
        for name in self.SCRIPTS:
            with self.subTest(name):
                self.assertNotRegex(self.code(name), r'\|\s*cmp\b')


if __name__ == '__main__':
    unittest.main()
