#!/usr/bin/env python3
"""Offline tests for the per-kernel eBPF module build (Track A).

Covers the kernel header guard, the pins and their pairing with the daemon,
the raw-blob ebpf_prog tree hash, the deterministic header handoff, the
eBPF handoff allowlists and sibling comparison, the assembly-side manifest,
and the image-side manifest check. No container, network or compiler.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
EBPF = FACTORY / 'snitchwatch-system-ebpf.py'
EBPF_PINS_PATH = FACTORY / 'snitchwatch-system-ebpf-pins.json'
PINS = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
DAEMON_PINS = json.loads((FACTORY / 'snitchwatch-system-daemon-pins.json').read_text())
CHECK = ROOT / 'system_files/usr/libexec/bazzite-tower-opensnitch-ebpf-check'
HANDOFF = FACTORY / 'snitchwatch-system-handoff.py'
LEGACY_COMMIT = 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc'
LEGACY_RPM = {'name': 'opensnitch', 'version': '1.8.0', 'release': '1', 'tag': 'v1.8.0', 'sha256': 'e' * 64}
R72 = '7.2.15-ogc1.1.fc45.x86_64'
R73 = '7.3.4-ogc6.1.fc45.x86_64'
BPF_HEADER = b'\x7fELF\x02\x01\x01' + bytes(9) + (1).to_bytes(2, 'little') + (247).to_bytes(2, 'little') \
    + (1).to_bytes(4, 'little') + bytes(52 - 24) + (64).to_bytes(2, 'little') + bytes(64 - 54)


def load(path):
    return runpy.run_path(str(path))


class Workdir(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='sw-ebpf.')
        self.addCleanup(work.cleanup)
        self.addCleanup(os.umask, os.umask(0o022))
        self.root = Path(work.name)
        self.e = load(EBPF)


class Pins(unittest.TestCase):
    def setUp(self):
        self.raw = EBPF_PINS_PATH.read_bytes()
        self.pins = json.loads(self.raw)
        self.e = load(EBPF)

    def test_pins_file_matches_the_reviewed_digest(self):
        # Any edit to the eBPF pins must also move this constant (no half-landed repin).
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.e['EXPECTED_EBPF_PINS_SHA256'])

    def test_toolchain_is_the_signed_llvm21_compat_build_only(self):
        rpms = self.pins['toolchainRpms']
        self.assertEqual(sorted(r['name'] for r in rpms), ['clang21', 'clang21-libs', 'clang21-resource-filesystem',
                                                           'llvm21', 'llvm21-filesystem', 'llvm21-libs'])
        for r in rpms:
            self.assertEqual(r['nevra'], '%s-0:21.1.8-10.fc45.x86_64' % r['name'])
            self.assertEqual(r['kojiPath'], 'llvm21/21.1.8/10.fc45/data/signed/f577861e/x86_64/' + r['file'])
            self.assertRegex(r['sha256'], '^[0-9a-f]{64}$')
            self.assertGreater(r['size'], 0)
        self.assertEqual(self.pins['rpmSigningKey'], DAEMON_PINS['rpmSigningKey'])
        self.assertEqual(self.pins['kojiBase'], DAEMON_PINS['kojiBase'])
        self.assertEqual(self.pins['clangVersion'], '21.1.8')
        self.assertEqual({k: v['path'] for k, v in self.pins['tools'].items()},
                         {'cc': '/usr/bin/clang-21', 'llc': '/usr/bin/llc-21', 'strip': '/usr/bin/llvm-strip-21'})
        self.assertNotIn('clang23', self.raw.decode())

    def test_modules_pair_with_the_daemon_that_loads_them(self):
        system = self.pins['profiles']['system']
        self.assertEqual(system['opensnitchCommit'], DAEMON_PINS['sourceCommit'])
        self.assertEqual(system['opensnitchCommit'], PINS['submoduleCommit'])
        self.assertEqual(system['ebpfProgTreeSha256'], 'c37c309a548ec5518aec25bd4602d7c5672fb8e94ab9430893615aeafe0e4537')
        legacy = self.pins['profiles']['legacy']
        self.assertEqual(legacy['opensnitchCommit'], 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')
        self.assertEqual(legacy['ebpfProgTreeSha256'], '39512718023e91e3d48bdaa6f0c958a5457ebb8df560dbd53d348f55551c6fd6')
        self.e['check_pairing'](self.pins, 'system', DAEMON_PINS, PINS)

    def test_pairing_refuses_a_half_landed_repin(self):
        for daemon, native in (({**DAEMON_PINS, 'sourceCommit': 'f' * 40}, PINS),
                               (DAEMON_PINS, {**PINS, 'submoduleCommit': 'f' * 40})):
            with self.assertRaises(self.e['Refusal']) as caught:
                self.e['check_pairing'](self.pins, 'system', daemon, native)
            self.assertIn('same opensnitch commit', str(caught.exception))

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

    def test_system_pairing_needs_its_daemon_pins_and_unknown_profiles_are_refused(self):
        with self.assertRaises(self.e['Refusal']):
            self.e['check_pairing'](self.pins, 'system')
        with self.assertRaises(self.e['Refusal']):
            self.e['check_pairing'](self.pins, 'other')

    def test_source_pin_names_the_one_commit_to_fetch(self):
        self.assertEqual(self.e['source_pin'](self.pins), {
            'repository': 'https://github.com/evilsocket/opensnitch.git', 'commit': 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc'})
        result = subprocess.run(['python3', '-B', str(EBPF), 'source-pin', str(EBPF_PINS_PATH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['commit'], 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')

    def test_check_rpm_accepts_only_the_pinned_rpm_file(self):
        rpm = EBPF_PINS_PATH.parent / 'nonexistent.rpm'
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

    def test_objects_are_the_three_the_daemon_loads(self):
        check = load(CHECK)
        self.assertEqual(sorted(self.pins['objects']), sorted(check['OBJECTS']))

    def test_make_command_is_the_verified_one(self):
        argv = self.e['make_argv'](self.pins, Path('/work/ebpf/build/ebpf_prog'), Path('/work/ebpf/kernel-headers'), R73)
        self.assertEqual(argv, ['make', '-B', '-R', '-r', '-C', '/work/ebpf/build/ebpf_prog',
                                'KERNEL_VER=7.3.4-ogc6.1.fc45', 'KERNEL_DIR=/work/ebpf/kernel-headers',
                                'KERNEL_HEADERS=/work/ebpf/kernel-headers', 'KERNEL_ARCH=x86_64',
                                'CC=/usr/bin/clang-21 -DCC_USING_FENTRY -fms-extensions -Wno-microsoft-anon-tag',
                                'LLC=/usr/bin/llc-21'])
        joined = ' '.join(argv)
        self.assertNotIn('EXTRA_FLAGS', joined)
        self.assertNotIn(' ARCH=', ' ' + joined)

    def test_the_build_helper_never_asks_the_container_for_its_kernel(self):
        text = EBPF.read_text()
        for needle in ('os.uname', 'platform.', "'uname'", 'uname -r', '/proc/sys/kernel/osrelease'):
            self.assertNotIn(needle, text)


class KernelGuard(unittest.TestCase):
    def setUp(self):
        self.e = load(EBPF)

    def guard(self, core=(0, R72 + '\n'), devel=(0, R72 + '\n'), src=(R72,), modules=(R72,)):
        return self.e['kernel_release'](core, devel, list(src), list(modules))

    def refused(self, **kwargs):
        with self.assertRaises(self.e['Refusal']) as caught:
            self.guard(**kwargs)
        return str(caught.exception)

    def test_matching_base_passes(self):
        self.assertEqual(self.guard(), R72)
        self.assertEqual(self.guard(core=(0, R73), devel=(0, R73), src=(R73,), modules=(R73,)), R73)

    def test_missing_kernel_devel_fails_and_never_falls_back(self):
        message = self.refused(devel=(1, 'package kernel-devel is not installed\n'), src=())
        self.assertIn('kernel-devel', message)
        self.assertIn(R72, message)
        self.assertIn('not a substitute', message)

    def test_devel_core_mismatch_names_both_versions(self):
        message = self.refused(devel=(0, R73 + '\n'))
        self.assertIn(R72, message)
        self.assertIn(R73, message)

    def test_header_dir_mismatch_names_both_versions(self):
        message = self.refused(src=(R73,))
        self.assertIn(R72, message)
        self.assertIn(R73, message)

    def test_modules_dir_mismatch_names_both_versions(self):
        message = self.refused(modules=(R73,))
        self.assertIn(R72, message)
        self.assertIn(R73, message)

    def test_two_kernels_are_refused(self):
        self.assertIn('exactly one', self.refused(core=(0, R72 + '\n' + R73 + '\n')))
        self.assertIn('exactly one', self.refused(modules=(R72, R73)))
        self.assertIn('exactly one', self.refused(src=(R72, R73)))

    def test_missing_kernel_core_or_odd_release_is_refused(self):
        self.refused(core=(1, 'package kernel-core is not installed\n'))
        self.refused(core=(0, '7.2.15-ogc1.1.fc45.aarch64\n'), devel=(0, '7.2.15-ogc1.1.fc45.aarch64\n'),
                     src=('7.2.15-ogc1.1.fc45.aarch64',), modules=('7.2.15-ogc1.1.fc45.aarch64',))
        self.refused(core=(0, '../x.x86_64\n'), devel=(0, '../x.x86_64\n'), src=('../x.x86_64',), modules=('../x.x86_64',))

    def config_root(self, core=b'CONFIG_BPF=y\n', devel=b'CONFIG_BPF=y\n'):
        tmp = tempfile.TemporaryDirectory(prefix='sw-ebpf-config.')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for path, data in ((root / 'usr/lib/modules' / R72 / 'config', core), (root / 'usr/src/kernels' / R72 / '.config', devel)):
            path.parent.mkdir(parents=True)
            if data is not None:
                path.write_bytes(data)
        return root

    def test_kernel_config_must_be_identical_in_core_and_devel(self):
        self.assertEqual(self.e['kernel_config_sha256'](self.config_root(), R72), hashlib.sha256(b'CONFIG_BPF=y\n').hexdigest())
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['kernel_config_sha256'](self.config_root(devel=b'CONFIG_BPF=n\n'), R72)
        self.assertIn('differs', str(caught.exception))
        with self.assertRaises((self.e['Refusal'], OSError)):
            self.e['kernel_config_sha256'](self.config_root(core=None), R72)

    def test_cli_fixture_fails_loudly_without_kernel_devel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bin_dir = Path(tmp) / 'root', Path(tmp) / 'bin'
            (root / 'usr/lib/modules' / R72).mkdir(parents=True)
            (root / 'usr/src/kernels').mkdir(parents=True)
            bin_dir.mkdir()
            fake = bin_dir / 'rpm'
            fake.write_text('#!/bin/sh\nfor a; do last=$a; done\n'
                            'if [ "$last" = kernel-core ]; then echo %s; exit 0; fi\n'
                            'echo "package $last is not installed"; exit 1\n' % R72)
            fake.chmod(0o755)
            result = subprocess.run(['python3', '-B', str(EBPF), 'kernel-export', '--root', str(root), str(Path(tmp) / 'out')],
                                    env={'PATH': '%s:/usr/bin:/bin' % bin_dir}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('kernel-devel', result.stderr)
            self.assertIn(R72, result.stderr)
            self.assertFalse((Path(tmp) / 'out').exists())


class TreeAndHeaders(Workdir):
    def make_tree(self, base):
        (base / 'include/linux').mkdir(parents=True)
        (base / 'include/linux/kconfig.h').write_text('#define X 1\n')
        (base / 'arch/x86/include').mkdir(parents=True)
        (base / 'arch/x86/include/a.h').write_text('a\n')
        (base / 'include/linux/alias.h').symlink_to('kconfig.h')
        return base

    def test_tree_hash_is_the_pins_env_method(self):
        tree = self.make_tree(self.root / 'ebpf_prog')
        (tree / 'include/linux/alias.h').unlink()
        reference = subprocess.run('find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum',
                                   shell=True, cwd=tree, capture_output=True, text=True, check=True).stdout.split()[0]
        self.assertEqual(self.e['tree_sha256'](tree), reference)

    def test_raw_blob_extraction_from_git(self):
        repo = self.root / 'repo'
        (repo / 'ebpf_prog').mkdir(parents=True)
        (repo / 'ebpf_prog/opensnitch.c').write_text('int x;\n')
        (repo / 'ebpf_prog/Makefile').write_text('all:\n')
        (repo / 'other.txt').write_text('no\n')
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        for args in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'x']):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True)
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        dest = self.root / 'scratch'
        self.e['extract_raw'](repo, head, dest)
        self.assertEqual(sorted(p.name for p in (dest / 'ebpf_prog').iterdir()), ['Makefile', 'opensnitch.c'])
        with self.assertRaises(self.e['Refusal']):
            self.e['extract_raw'](repo, 'f' * 40, self.root / 'scratch2')

    def test_header_tarball_is_deterministic_and_round_trips(self):
        tree = self.make_tree(self.root / 'kernel')
        one, two = self.root / 'one.tar', self.root / 'two.tar'
        self.e['pack_tree'](tree, one)
        os.utime(tree / 'include/linux/kconfig.h', (1, 1))
        self.e['pack_tree'](tree, two)
        self.assertEqual(one.read_bytes(), two.read_bytes())
        dest = self.root / 'unpacked'
        self.e['unpack_tree'](one, dest)
        self.assertEqual((dest / 'include/linux/kconfig.h').read_text(), '#define X 1\n')
        self.assertEqual(os.readlink(dest / 'include/linux/alias.h'), 'kconfig.h')

    def test_header_tarball_refuses_links_out_of_the_tree_and_special_bits(self):
        tree = self.make_tree(self.root / 'kernel')
        (tree / 'escape.h').symlink_to('/etc/passwd')
        with self.assertRaises(self.e['Refusal']):
            self.e['pack_tree'](tree, self.root / 'bad.tar')
        (tree / 'escape.h').unlink()
        (tree / 'up.h').symlink_to('../../outside')
        with self.assertRaises(self.e['Refusal']):
            self.e['pack_tree'](tree, self.root / 'bad2.tar')
        (tree / 'up.h').unlink()
        (tree / 'arch/x86/include/a.h').chmod(0o4755)
        with self.assertRaises(self.e['Refusal']):
            self.e['pack_tree'](tree, self.root / 'bad3.tar')

    def test_unpack_refuses_members_that_leave_the_destination(self):
        bad = self.root / 'evil.tar'
        with tarfile.open(bad, 'w') as tar:
            info = tarfile.TarInfo('../evil.h')
            info.size = 1
            tar.addfile(info, io.BytesIO(b'x'))
        with self.assertRaises(Exception):
            self.e['unpack_tree'](bad, self.root / 'dest')
        self.assertFalse((self.root / 'evil.h').exists())

    def test_kernel_unpack_checks_the_facts_digest(self):
        tree = self.make_tree(self.root / 'kernel')
        handoff = self.root / 'handoff'
        handoff.mkdir()
        self.e['pack_tree'](tree, handoff / 'kernel-headers.tar')
        facts = {'schemaVersion': 1, 'kernelRelease': R72, 'kernelCore': R72, 'kernelDevel': R72,
                 'headersTarSha256': hashlib.sha256((handoff / 'kernel-headers.tar').read_bytes()).hexdigest(),
                 'kernelConfigSha256': 'c' * 64}
        (handoff / 'kernel-facts.json').write_text(json.dumps(facts))
        self.assertEqual(self.e['kernel_unpack'](handoff, self.root / 'h1')['kernelRelease'], R72)
        (handoff / 'kernel-facts.json').write_text(json.dumps({**facts, 'headersTarSha256': '0' * 64}))
        with self.assertRaises(self.e['Refusal']):
            self.e['kernel_unpack'](handoff, self.root / 'h2')
        (handoff / 'kernel-facts.json').write_text(json.dumps({**facts, 'kernelDevel': R73}))
        with self.assertRaises(self.e['Refusal']):
            self.e['kernel_unpack'](handoff, self.root / 'h3')


class EbpfHandoff(Workdir):
    def setUp(self):
        super().setUp()
        self.h = load(HANDOFF)
        self.pins = json.loads(EBPF_PINS_PATH.read_text())
        self.owner = (os.getuid(), os.getgid())

    def write_stage(self, root, content=b'', log=True):
        root.mkdir()
        for name in self.pins['objects']:
            (root / name).write_bytes(BPF_HEADER + name.encode() + content)
        facts = {'schemaVersion': 1, 'kernelRelease': R72, 'kernelCore': R72, 'kernelDevel': R72, 'headersTarSha256': 'a' * 64,
                 'kernelConfigSha256': 'c' * 64}
        (root / 'kernel-facts.json').write_text(json.dumps(facts, sort_keys=True))
        direct = self.root / ('direct-' + root.name)
        direct.mkdir()
        (direct / 'kernel-facts.json').write_text(json.dumps(facts, sort_keys=True))
        self.direct = direct / 'kernel-facts.json'
        (root / 'build-info.json').write_text(json.dumps({
            'clang': 'clang version 21.1.8 (Fedora 21.1.8-10.fc45)', 'kernelRelease': R72,
            'opensnitchCommit': self.pins['profiles']['system']['opensnitchCommit'],
            'ebpfProgTreeSha256': self.pins['profiles']['system']['ebpfProgTreeSha256'], 'rpmInventorySha256': 'd' * 64},
            sort_keys=True))
        if log:
            (root / 'build.log').write_text('log\n')
        for path in root.iterdir():
            path.chmod(0o644)
        return root

    def test_allowlists(self):
        objects = set(self.pins['objects'])
        self.assertEqual(set(self.h['allowlist']('ebpf', self.pins)),
                         objects | {'kernel-facts.json', 'build-info.json', 'build.log'})
        self.assertEqual(set(self.h['allowlist']('ebpf-rebuild', self.pins)), objects | {'kernel-facts.json', 'build-info.json'})
        self.assertEqual(set(self.h['allowlist']('ebpf-kernel')), {'kernel-facts.json', 'kernel-headers.tar'})
        self.assertTrue(all(mode == 0o644 for mode in self.h['allowlist']('ebpf', self.pins).values()))

    def test_export_then_check_and_extra_files_are_refused(self):
        src = self.write_stage(self.root / 'src')
        dest = self.root / 'dest'
        self.h['export']('ebpf', src, dest, self.pins)
        self.h['check']('ebpf', dest, self.pins, owner=self.owner)
        (dest / 'extra.o').write_bytes(b'x')
        with self.assertRaises(self.h['Refusal']):
            self.h['check']('ebpf', dest, self.pins, owner=self.owner)

    def test_compare_accepts_only_byte_identical_builds(self):
        first = self.write_stage(self.root / 'first')
        second = self.write_stage(self.root / 'second', log=False)
        result = self.h['compare_ebpf'](first, second, self.pins)
        self.assertTrue(result['identical'])
        (second / 'opensnitch.o').write_bytes(BPF_HEADER + b'different')
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['compare_ebpf'](first, second, self.pins)
        self.assertIn('opensnitch.o', str(caught.exception))
        self.assertIn(hashlib.sha256(BPF_HEADER + b'different').hexdigest(), str(caught.exception))

    def test_compare_refuses_the_same_file_twice(self):
        first = self.write_stage(self.root / 'first')
        with self.assertRaises(self.h['Refusal']):
            self.h['compare_ebpf'](first, first, self.pins)

    def test_compare_refuses_differing_kernel_facts(self):
        first = self.write_stage(self.root / 'first')
        second = self.write_stage(self.root / 'second', log=False)
        (second / 'kernel-facts.json').write_text('{}')
        with self.assertRaises(self.h['Refusal']):
            self.h['compare_ebpf'](first, second, self.pins)

    def test_assembly_manifest_is_computed_from_pins_facts_and_its_own_hashes(self):
        handoff = self.write_stage(self.root / 'ebpf')
        out = self.root / 'overlay'
        manifest = self.e['stage_overlay'](EBPF_PINS_PATH, 'system', DAEMON_PINS, PINS, handoff, out, self.direct)
        staged = out / 'usr/share/bazzite-tower'
        on_disk = json.loads((staged / 'ebpf-manifest.json').read_text())
        self.assertEqual(manifest, on_disk)
        self.assertEqual(on_disk['kernelRelease'], R72)
        self.assertEqual(on_disk['opensnitchCommit'], DAEMON_PINS['sourceCommit'])
        self.assertEqual(on_disk['clangVersion'], '21.1.8')
        self.assertEqual(on_disk['objectsDir'], '/usr/lib/opensnitchd/ebpf')
        self.assertEqual(on_disk['ebpfPinsSha256'], hashlib.sha256(EBPF_PINS_PATH.read_bytes()).hexdigest())
        self.assertEqual(on_disk['kernelConfigSha256'], 'c' * 64)
        self.assertEqual(on_disk['compileRpmInventorySha256'], 'd' * 64)
        for name in self.pins['objects']:
            data = (staged / 'opensnitch-ebpf' / name).read_bytes()
            self.assertEqual(on_disk['objects'][name], hashlib.sha256(data).hexdigest())
            self.assertEqual(os.stat(staged / 'opensnitch-ebpf' / name).st_mode & 0o7777, 0o644)

    def test_legacy_assembly_manifest_pairs_with_the_rpm_and_needs_no_system_pins(self):
        legacy = self.pins['profiles']['legacy']
        handoff = self.write_stage(self.root / 'ebpf')
        info = json.loads((handoff / 'build-info.json').read_text())
        (handoff / 'build-info.json').write_text(json.dumps({**info, 'opensnitchCommit': legacy['opensnitchCommit'],
                                                             'ebpfProgTreeSha256': legacy['ebpfProgTreeSha256']}))
        manifest = self.e['stage_overlay'](EBPF_PINS_PATH, 'legacy', None, None, handoff, self.root / 'overlay', self.direct)
        self.assertEqual(manifest['profile'], 'legacy')
        self.assertEqual(manifest['opensnitchCommit'], 'b404c4c6316760fa7bc415509d3f8d747f7dc9cc')
        self.assertEqual(manifest['ebpfProgTreeSha256'], '39512718023e91e3d48bdaa6f0c958a5457ebb8df560dbd53d348f55551c6fd6')
        self.assertEqual(manifest['daemonRpm'], {**legacy['daemonRpm'], 'tag': 'v1.8.0'})
        self.assertEqual(manifest['objectsDir'], '/usr/lib/opensnitchd/ebpf')
        self.assertTrue((self.root / 'overlay/usr/share/bazzite-tower/opensnitch-ebpf/opensnitch.o').is_file())
        # The system manifest gains no RPM field (system behaviour is unchanged).
        system = self.e['stage_overlay'](EBPF_PINS_PATH, 'system', DAEMON_PINS, PINS, self.write_stage(self.root / 'ebpf2'),
                                         self.root / 'overlay2', self.direct)
        self.assertNotIn('daemonRpm', system)

    def test_legacy_assembly_refuses_modules_built_from_the_system_commit(self):
        handoff = self.write_stage(self.root / 'ebpf')  # build-info names the system commit
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['stage_overlay'](EBPF_PINS_PATH, 'legacy', None, None, handoff, self.root / 'overlay', self.direct)
        self.assertIn('opensnitchCommit', str(caught.exception))

    def test_stage_cli_needs_system_pins_only_for_the_system_profile(self):
        legacy = self.pins['profiles']['legacy']
        handoff = self.write_stage(self.root / 'ebpf')
        info = json.loads((handoff / 'build-info.json').read_text())
        (handoff / 'build-info.json').write_text(json.dumps({**info, 'opensnitchCommit': legacy['opensnitchCommit'],
                                                             'ebpfProgTreeSha256': legacy['ebpfProgTreeSha256']}))
        base = ['python3', '-B', str(EBPF), 'stage', '--pins', str(EBPF_PINS_PATH), '--kernel-facts', str(self.direct)]
        ok = subprocess.run([*base, '--profile', 'legacy', str(handoff), str(self.root / 'out-legacy')], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        refused = subprocess.run([*base, '--profile', 'system', str(handoff), str(self.root / 'out-system')], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('ebpf: stage:', refused.stderr)
        self.assertIn('daemon and snitchwatch pins', refused.stderr)

    def test_assembly_manifest_refuses_inconsistent_build_info(self):
        for key, value in (('kernelRelease', R73), ('clang', 'clang version 23.1.5'),
                           ('opensnitchCommit', 'f' * 40), ('ebpfProgTreeSha256', '0' * 64), ('rpmInventorySha256', '')):
            with self.subTest(key):
                handoff = self.write_stage(self.root / ('ebpf-' + key))
                info = json.loads((handoff / 'build-info.json').read_text())
                (handoff / 'build-info.json').write_text(json.dumps({**info, key: value}))
                with self.assertRaises(self.e['Refusal']):
                    self.e['stage_overlay'](EBPF_PINS_PATH, 'system', DAEMON_PINS, PINS, handoff, self.root / ('out-' + key),
                                            self.direct)


    def test_assembly_refuses_compile_facts_that_differ_from_the_kernel_stage(self):
        handoff = self.write_stage(self.root / 'ebpf')
        facts = json.loads(self.direct.read_text())
        self.direct.write_text(json.dumps({**facts, 'kernelConfigSha256': 'e' * 64}, sort_keys=True))
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['stage_overlay'](EBPF_PINS_PATH, 'system', DAEMON_PINS, PINS, handoff, self.root / 'out', self.direct)
        self.assertIn('differ from the ebpf-kernel stage facts', str(caught.exception))

    def test_ebpf_handoff_check_bounds_file_sizes(self):
        src = self.write_stage(self.root / 'src')
        dest = self.root / 'dest'
        self.h['export']('ebpf', src, dest, self.pins)
        with open(dest / 'opensnitch.o', 'ab') as grow:
            grow.truncate(self.h['MAX_EBPF_FILE'] + 1)
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['check']('ebpf', dest, self.pins, owner=self.owner)
        self.assertIn('bound', str(caught.exception))
        kernel = self.root / 'kernel'
        kernel.mkdir()
        (kernel / 'kernel-facts.json').write_text('{}')
        with open(kernel / 'kernel-headers.tar', 'wb') as big:
            big.truncate(self.h['MAX_EBPF_HEADERS'] + 1)
        for path in kernel.iterdir():
            path.chmod(0o644)
        with self.assertRaises(self.h['Refusal']):
            self.h['check']('ebpf-kernel', kernel, owner=self.owner)
        (kernel / 'kernel-headers.tar').unlink()
        with open(kernel / 'kernel-headers.tar', 'wb') as fits:
            fits.truncate(130 * 1024 * 1024)
        (kernel / 'kernel-headers.tar').chmod(0o644)
        self.h['check']('ebpf-kernel', kernel, owner=self.owner)


class Guards(Workdir):
    """Each refusal below goes red if its guard is deleted (mutation-checked)."""

    def git_repo(self, files, modes=None):
        repo = self.root / 'repo'
        (repo / 'ebpf_prog').mkdir(parents=True)
        for name, data in files.items():
            path = repo / 'ebpf_prog' / name
            if isinstance(data, Path):
                path.symlink_to(data)
            else:
                path.write_text(data)
            if modes and name in modes:
                path.chmod(modes[name])
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        for args in (['init', '-q'], ['config', 'core.fileMode', 'true'], ['add', '.'], ['commit', '-qm', 'x']):
            subprocess.run(['git', '-C', str(repo), *args], env=env, check=True)
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True)
        return repo, head.stdout.strip()

    def test_load_pins_refuses_a_tampered_copy(self):
        tampered = self.root / 'pins.json'
        tampered.write_bytes(EBPF_PINS_PATH.read_bytes().replace(b'21.1.8', b'23.1.5', 1))
        with self.assertRaises(self.e['Refusal']):
            self.e['load_pins'](tampered)
        self.e['load_pins'](EBPF_PINS_PATH)
        result = subprocess.run(['python3', '-B', str(EBPF), 'check-pins', str(tampered)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_extract_raw_refuses_executable_blobs(self):
        repo, head = self.git_repo({'a.c': 'x\n', 'run.sh': 'y\n'}, modes={'run.sh': 0o755})
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['extract_raw'](repo, head, self.root / 'out')
        self.assertIn('100755', str(caught.exception))

    def test_extract_raw_refuses_symlink_blobs(self):
        repo, head = self.git_repo({'a.c': 'x\n', 'link.h': Path('/etc/passwd')})
        with self.assertRaises(self.e['Refusal']) as caught:
            self.e['extract_raw'](repo, head, self.root / 'out')
        self.assertIn('120000', str(caught.exception))

    def test_tree_hash_refuses_non_regular_files(self):
        tree = self.root / 'tree'
        tree.mkdir()
        (tree / 'a.c').write_text('x')
        (tree / 'b.c').symlink_to('a.c')
        with self.assertRaises(self.e['Refusal']):
            self.e['tree_sha256'](tree)

    def fake_tools(self, owner='clang21-0:21.1.8-10.fc45.x86_64', version='21.1.8', verify_out='', verify_only=None):
        bin_dir = self.root / ('bin-%d' % len(list(self.root.iterdir())))
        bin_dir.mkdir()
        pins = json.loads(EBPF_PINS_PATH.read_text())
        llvm = 'llvm21-0:21.1.8-10.fc45.x86_64'
        for key, banner in (('cc', 'clang version %s (Fedora)' % version), ('llc', '  LLVM version %s' % version),
                            ('strip', '  LLVM version %s' % version)):
            path = bin_dir / key
            path.write_text('#!/bin/sh\necho "%s"\n' % banner)
            path.chmod(0o755)
            pins['tools'][key]['path'] = str(path)
        (bin_dir / 'rpm').write_text('#!/bin/sh\n'
                                     'if [ "$1" = -V ]; then case "$2" in %s) printf "%%s" "%s";; esac; exit 0; fi\n'
                                     'for a; do last=$a; done\n'
                                     'case "$last" in */cc) printf "%%s" "%s";; *) printf "%%s" "%s";; esac\n'
                                     % (verify_only or '*', verify_out, owner, llvm))
        (bin_dir / 'rpm').chmod(0o755)
        return pins, {**self.e['SAFE_ENV'], 'PATH': '%s:/usr/bin:/bin' % bin_dir}

    def test_check_tools_accepts_the_pinned_owner_and_version(self):
        pins, env = self.fake_tools()
        with mock.patch.dict(self.e['check_tools'].__globals__, {'SAFE_ENV': env}):
            self.assertEqual(self.e['check_tools'](pins), 'clang version 21.1.8 (Fedora)')

    def test_check_tools_refuses_a_foreign_owner_wrong_version_or_modified_files(self):
        for kwargs, needle in (({'owner': 'clang-0:23.1.5-1.fc45.x86_64'}, 'not owned'),
                               ({'version': '23.1.5'}, 'does not report'),
                               ({'verify_out': 'S.5....T.  /usr/bin/clang-21'}, 'rpm -V'),
                               ({'verify_out': 'S.5....T.  /usr/lib64/libclang-cpp.so.21', 'verify_only': 'clang21-libs|llvm21-libs'}, 'rpm -V')):
            with self.subTest(needle):
                pins, env = self.fake_tools(**kwargs)
                with mock.patch.dict(self.e['check_tools'].__globals__, {'SAFE_ENV': env}):
                    with self.assertRaises(self.e['Refusal']) as caught:
                        self.e['check_tools'](pins)
                self.assertIn(needle, str(caught.exception))

    def build_fixture(self, made):
        repo, head = self.git_repo({'opensnitch.c': 'int x;\n', 'Makefile': 'all:\n'})
        pins = json.loads(EBPF_PINS_PATH.read_text())
        probe = self.root / 'tree-probe'
        self.e['extract_raw'](repo, head, probe)
        pins['profiles']['system'] = {'opensnitchCommit': head, 'ebpfProgTreeSha256': self.e['tree_sha256'](probe / 'ebpf_prog')}
        kernel, headers = self.root / 'kernel', self.root / 'headers'
        (headers / 'include').mkdir(parents=True)
        kernel.mkdir()
        self.e['pack_tree'](headers, kernel / 'kernel-headers.tar')
        (kernel / 'kernel-facts.json').write_text(json.dumps({
            'schemaVersion': 1, 'kernelRelease': R72, 'kernelCore': R72, 'kernelDevel': R72, 'kernelConfigSha256': 'c' * 64,
            'headersTarSha256': hashlib.sha256((kernel / 'kernel-headers.tar').read_bytes()).hexdigest()}))
        bin_dir = self.root / 'fakebin'
        bin_dir.mkdir()
        touches = ' '.join('"$dir/%s"' % name for name in made)
        (bin_dir / 'make').write_text('#!/bin/sh\nfor a; do case "$prev" in -C) dir=$a;; esac; prev=$a; done\n'
                                      'touch %s\n' % touches)
        (bin_dir / 'make').chmod(0o755)
        inventory = self.root / 'inventory.txt'
        inventory.write_text('x\n')
        env = {**self.e['SAFE_ENV'], 'PATH': '%s:/usr/bin:/bin' % bin_dir}
        return pins, {**DAEMON_PINS, 'sourceCommit': head}, {**PINS, 'submoduleCommit': head}, repo, kernel, inventory, env

    def run_build(self, pins, daemon, native, repo, kernel, inventory, env):
        patched = {'SAFE_ENV': env, 'check_tools': lambda pins: 'clang version 21.1.8 (Fedora)'}
        with mock.patch.dict(self.e['build'].__globals__, patched):
            return self.e['build'](pins, 'system', daemon, native, repo, kernel, self.root / 'work', self.root / 'out', inventory)

    def test_build_refuses_a_tree_that_differs_from_the_pin(self):
        pins, *rest = self.build_fixture(['opensnitch.o', 'opensnitch-procs.o', 'opensnitch-dns.o'])
        pins['profiles']['system']['ebpfProgTreeSha256'] = '0' * 64
        with self.assertRaises(self.e['Refusal']) as caught:
            self.run_build(pins, *rest)
        self.assertIn('differs from the pin', str(caught.exception))

    def test_build_refuses_a_wrong_object_set(self):
        fixture = self.build_fixture(['opensnitch.o', 'opensnitch-dns.o'])
        with self.assertRaises(self.e['Refusal']) as caught:
            self.run_build(*fixture)
        self.assertIn('want exactly', str(caught.exception))


class ImageCheck(Workdir):
    def setUp(self):
        super().setUp()
        self.c = load(CHECK)

    def image(self, profile='system', modules=(R72,), manifest=True, kernel=R72, commit=None, rpm='default'):
        root = self.root / ('img-%d' % len(list(self.root.iterdir())))
        objects = root / 'usr/lib/opensnitchd/ebpf'
        objects.mkdir(parents=True)
        hashes = {}
        for name in self.c['OBJECTS']:
            data = BPF_HEADER + name.encode()
            (objects / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        for release in modules:
            (root / 'usr/lib/modules' / release).mkdir(parents=True)
            (root / 'usr/lib/modules' / release / 'config').write_bytes(b'CONFIG_BPF=y\n')
        share = root / 'usr/share/bazzite-tower'
        share.mkdir(parents=True)
        (share / 'snitchwatch-bridge-profile').write_text(profile + '\n')
        if manifest:
            (share / 'ebpf-manifest.json').write_text(json.dumps({
                'schemaVersion': 1, 'profile': profile, 'kernelRelease': kernel, 'objects': hashes,
                'objectsDir': '/usr/lib/opensnitchd/ebpf', 'ebpfProgTreeSha256': 'b' * 64,
                'opensnitchCommit': commit or (LEGACY_COMMIT if profile == 'legacy' else 'a' * 40),
                'clangVersion': '21.1.8', 'kernelConfigSha256': hashlib.sha256(b'CONFIG_BPF=y\n').hexdigest(),
                **({'daemonRpm': LEGACY_RPM if rpm == 'default' else rpm} if profile == 'legacy' and rpm is not None else {})}))
        return root

    def run_check(self, root):
        return subprocess.run(['python3', '-B', str(CHECK), '--root', str(root)], capture_output=True, text=True)

    def test_system_image_with_matching_manifest_passes(self):
        result = self.run_check(self.image())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(R72, result.stdout)

    def test_manifest_kernel_must_be_the_image_kernel(self):
        result = self.run_check(self.image(kernel=R73))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(R73, result.stderr)
        self.assertIn(R72, result.stderr)

    def test_manifest_kernel_config_must_be_the_image_kernel_config(self):
        root = self.image()
        (root / 'usr/lib/modules' / R72 / 'config').write_bytes(b'CONFIG_BPF=n\n')
        result = self.run_check(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('kernel config', result.stderr)

    def test_image_must_ship_exactly_one_kernel(self):
        self.assertNotEqual(self.run_check(self.image(modules=(R72, R73))).returncode, 0)
        self.assertNotEqual(self.run_check(self.image(modules=())).returncode, 0)

    def test_on_disk_objects_must_match_the_manifest(self):
        root = self.image()
        (root / 'usr/lib/opensnitchd/ebpf/opensnitch-dns.o').write_bytes(BPF_HEADER + b'rpm copy')
        result = self.run_check(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('opensnitch-dns.o', result.stderr)

    def test_system_and_legacy_both_require_the_manifest(self):
        # INTENT CHANGE: legacy used to be required to carry NO manifest (RPM objects); it is now built like system.
        self.assertNotEqual(self.run_check(self.image(manifest=False)).returncode, 0)
        missing = self.run_check(self.image(profile='legacy', manifest=False))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('ebpf-manifest.json', missing.stderr)

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

    def test_a_manifest_for_the_other_profile_is_refused(self):
        root = self.image(profile='legacy')
        (root / 'usr/share/bazzite-tower/snitchwatch-bridge-profile').write_text('system\n')
        self.assertNotEqual(self.run_check(root).returncode, 0)

    def test_unknown_or_missing_profile_is_refused(self):
        root = self.image(profile='other')
        self.assertNotEqual(self.run_check(root).returncode, 0)
        (root / 'usr/share/bazzite-tower/snitchwatch-bridge-profile').unlink()
        self.assertNotEqual(self.run_check(root).returncode, 0)

    def test_elf_checks_still_run(self):
        root = self.image()
        (root / 'usr/lib/opensnitchd/ebpf/opensnitch.o').write_bytes(b'not elf')
        self.assertNotEqual(self.run_check(root).returncode, 0)


class Wiring(unittest.TestCase):
    def test_firewall_step_installs_built_objects_before_the_check(self):
        text = (ROOT / 'build_files/build.d/95-firewall.sh').read_text()
        cpio = text.index('rpm2cpio')
        install = text.index('/usr/share/bazzite-tower/opensnitch-ebpf/')
        check = text.index('python3 /usr/libexec/bazzite-tower-opensnitch-ebpf-check')
        self.assertLess(cpio, install)
        self.assertLess(install, check)

    def firewall_text(self):
        return (ROOT / 'build_files/build.d/95-firewall.sh').read_text()

    def test_firewall_step_installs_the_built_objects_for_both_profiles(self):
        text = self.firewall_text()
        install = text.index('/usr/share/bazzite-tower/opensnitch-ebpf/')
        # The loop is no longer inside an `if system` block: the preceding non-comment line must not gate on the bridge.
        before = [line for line in text[:install].splitlines() if line.strip() and not line.lstrip().startswith('#')]
        self.assertTrue(before[-2].lstrip().startswith('for object in '), before[-2:])
        self.assertNotIn('== system', before[-1])
        self.assertNotIn('== system', before[-2])

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

    def test_portmaster_variant_drops_the_staged_modules(self):
        text = self.firewall_text()
        branch = text[text.index('    portmaster)'):text.index('source "$(dirname')]
        self.assertIn('/usr/share/bazzite-tower/opensnitch-ebpf', branch)
        self.assertIn('/usr/share/bazzite-tower/ebpf-manifest.json', branch)
        smoke = (ROOT / 'tests/smoke.sh').read_text()
        portmaster = smoke[smoke.index('\nportmaster)'):]
        self.assertIn('/usr/share/bazzite-tower/ebpf-manifest.json', portmaster)

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

    def test_object_names_agree_across_firewall_step_check_pins_and_smoke(self):
        text = (ROOT / 'build_files/build.d/95-firewall.sh').read_text()
        loop = next(line for line in text.splitlines() if line.strip().startswith('for object in '))
        names = loop.strip()[len('for object in '):].split(';')[0].split()
        pins = json.loads(EBPF_PINS_PATH.read_text())
        self.assertEqual(sorted(names), sorted(load(CHECK)['OBJECTS']))
        self.assertEqual(sorted(names), sorted(pins['objects']))
        smoke = (ROOT / 'tests/smoke.sh').read_text()
        smoke_loop = next(line for line in smoke.splitlines() if line.strip().startswith('for ebpf_object in '))
        self.assertEqual(sorted(smoke_loop.strip()[len('for ebpf_object in '):].split(';')[0].split()), sorted(names))

    def test_smoke_asserts_the_manifest_independently(self):
        smoke = (ROOT / 'tests/smoke.sh').read_text()
        system = smoke[smoke.index('\nsystem)'):]
        self.assertIn('/usr/share/bazzite-tower/ebpf-manifest.json', system)
        self.assertIn('kernelRelease', system)
        self.assertIn('sha256sum', system)

    def test_tests_run_in_both_build_workflow_blocks(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-snitchwatch-ebpf.py'), 2)


if __name__ == '__main__':
    unittest.main()
