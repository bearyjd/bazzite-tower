#!/usr/bin/env python3
"""H1: the snitchwatch-system build is split into stages so that the bridge's
third-party build scripts cannot reach the scanner, the daemon or the assembly.

Static Containerfile/workflow structure plus behavioural tests of the stage
guards (snitchwatch-system-handoff.py) and of stage.py's unprivileged bridge
execution. Runs offline with python3 only (no rpm, cargo, dnf or containers).
What only a real CI build can prove is listed in docs/CODEMAPS/image-build.md.
"""
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'build_files/firewall'
CONTAINERFILE = ROOT / 'Containerfile'
PINS = json.loads((FACTORY / 'snitchwatch-system-pins.json').read_text())
BUILD_STAGES = ('snitchwatch-prep', 'snitchwatch-scanner', 'snitchwatch-bridge', 'snitchwatch-daemon', 'snitchwatch-system')
OFFLINE_STAGES = ('snitchwatch-scanner', 'snitchwatch-bridge', 'snitchwatch-system')
ONLINE_STAGES = ('snitchwatch-prep', 'snitchwatch-daemon')
OFFLINE_SCRIPTS = ('snitchwatch-system-scanner-stage.sh', 'snitchwatch-system-bridge-build.sh', 'snitchwatch-system-assemble.sh')
BRIDGE_SCRIPT = FACTORY / 'snitchwatch-system-bridge-build.sh'
# Scripts and fragments that execute third-party build code or the bridge binary.
BRIDGE_ONLY = ('snitchwatch-system-bridge-build.sh', 'build-bridge.sh', 'repro-check.sh', '--run-flags', '--check-ldd')
HANDOFF = FACTORY / 'snitchwatch-system-handoff.py'


def instructions(text):
    """[(keyword, rest)] with continuation lines joined and comments dropped."""
    result, pending = [], ''
    for line in text.splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith('#')):
            continue
        pending += line.rstrip()
        if pending.endswith('\\'):
            pending = pending[:-1] + ' '
            continue
        keyword, _, rest = pending.strip().partition(' ')
        result.append((keyword.upper(), rest.strip()))
        pending = ''
    return result


def stages(text=None):
    """{name: {'from': base, 'body': [(keyword, rest)], 'index': n}} in file order."""
    found, current = {}, None
    for keyword, rest in instructions(CONTAINERFILE.read_text() if text is None else text):
        if keyword == 'FROM':
            parts = rest.split()
            name = parts[2] if len(parts) == 3 and parts[1].upper() == 'AS' else '<final-%d>' % len(found)
            current = found[name] = {'from': parts[0], 'body': [], 'index': len(found)}
        elif current is not None:
            current['body'].append((keyword, rest))
    return found


def from_chain(all_stages, name):
    chain = []
    while name in all_stages:
        chain.append(name)
        name = all_stages[name]['from']
    return chain


def copy_sources(stage):
    return [re.match(r'--from=(\S+)', rest).group(1) for keyword, rest in stage['body']
            if keyword == 'COPY' and rest.startswith('--from=')]


def runs(stage):
    return [rest for keyword, rest in stage['body'] if keyword == 'RUN']


def script_text(run):
    """The RUN text plus the content of every factory script it invokes."""
    text = run
    for name in re.findall(r'/(?:factory|tools)/(snitchwatch-system-[\w.-]+)', run):
        text += '\n' + (FACTORY / name).read_text()
    return text


class ContainerfileStages(unittest.TestCase):
    def setUp(self):
        self.stages = stages()

    def test_stage_names_order_and_from_chain(self):
        names = [name for name in self.stages if not name.startswith('<final')]
        self.assertEqual(names, ['ctx', 'snitchwatch-legacy', *BUILD_STAGES, 'snitchwatch-selected'])
        builder = PINS['builderImage']
        self.assertEqual(self.stages['snitchwatch-prep']['from'], builder)
        daemon_pins = json.loads((FACTORY / 'snitchwatch-system-daemon-pins.json').read_text())
        self.assertEqual(daemon_pins['builderImage'], builder)
        for name in BUILD_STAGES[1:]:
            self.assertEqual(self.stages[name]['from'], 'snitchwatch-prep', name)
        self.assertEqual(self.stages['snitchwatch-legacy']['from'], 'scratch')
        self.assertEqual(self.stages['ctx']['from'], 'scratch')
        self.assertEqual(self.stages['snitchwatch-selected']['from'], 'snitchwatch-${SNITCHWATCH_BRIDGE}')
        final = [stage for name, stage in self.stages.items() if name.startswith('<final')]
        self.assertEqual([stage['from'] for stage in final], ['${BASE_IMAGE}'])
        self.assertIn(('COPY', '--from=snitchwatch-selected /out/ /'), final[0]['body'])

    def test_no_bridge_build_in_the_ancestry_of_scanner_daemon_or_assembly(self):
        for name in ('snitchwatch-scanner', 'snitchwatch-daemon', 'snitchwatch-system'):
            chain = from_chain(self.stages, name)
            self.assertNotIn('snitchwatch-bridge', chain, name)
            for ancestor in chain:
                for run in runs(self.stages[ancestor]):
                    text = script_text(run)
                    for needle in BRIDGE_ONLY:
                        self.assertNotIn(needle, text, '%s (ancestor of %s) reaches %s' % (ancestor, name, needle))
        # The bridge stage is the base of nothing and is read only by the assembly COPY.
        self.assertEqual([n for n, s in self.stages.items() if s['from'] == 'snitchwatch-bridge'], [])
        readers = [n for n, s in self.stages.items() if 'snitchwatch-bridge' in copy_sources(s)]
        self.assertEqual(readers, ['snitchwatch-system'])
        self.assertTrue(any('snitchwatch-system-bridge-build.sh' in run for run in runs(self.stages['snitchwatch-bridge'])))
        self.assertTrue(any('snitchwatch-system-daemon-build.sh' in run for run in runs(self.stages['snitchwatch-daemon'])))

    def test_daemon_build_runs_only_in_the_daemon_stage(self):
        for name, stage in self.stages.items():
            for run in runs(stage):
                if 'daemon-build.sh' in script_text(run):
                    self.assertEqual(name, 'snitchwatch-daemon')

    def test_daemon_stage_checks_and_exports_its_handoff_through_the_allowlist(self):
        run = runs(self.stages['snitchwatch-daemon'])[0]
        build = 'bash /factory/snitchwatch-system-daemon-build.sh /work/daemon-out'
        check = 'python3 /factory/snitchwatch-system-handoff.py check daemon --daemon-verifier /factory/snitchwatch-system-daemon-verify.py /work/daemon-out'
        export = 'python3 /factory/snitchwatch-system-handoff.py export daemon --daemon-verifier /factory/snitchwatch-system-daemon-verify.py /work/daemon-out /handoff/daemon'
        for needle in (build, check, export):
            self.assertIn(needle, run)
        self.assertLess(run.index(build), run.index(check))
        self.assertLess(run.index(check), run.index(export))
        self.assertEqual(run.count('&&'), 2)

    def test_network_none_is_on_every_offline_run_and_confined_to_those_stages(self):
        # M1 step 1 replaced `count('--network=') == 2` with exact stage sets.
        self.assertEqual(set(OFFLINE_STAGES) | set(ONLINE_STAGES), set(BUILD_STAGES))
        self.assertFalse(set(OFFLINE_STAGES) & set(ONLINE_STAGES))
        offline = set()
        for name, stage in self.stages.items():
            for run in runs(stage):
                if name in OFFLINE_STAGES:
                    self.assertTrue(run.startswith('--network=none '), name)
                    self.assertEqual(run.count('--network'), 1, name)
                    offline.add(name)
                else:
                    # Confinement: a builder that rejects the flag breaks only these legs' stages.
                    self.assertNotIn('--network', run, name)
            if name in OFFLINE_STAGES:
                self.assertEqual(len(runs(stage)), 1, name)
        self.assertEqual(offline, set(OFFLINE_STAGES))
        for name in ONLINE_STAGES:
            self.assertTrue(runs(self.stages[name]), name)
        self.assertEqual(CONTAINERFILE.read_text().count('--network='), len(OFFLINE_STAGES))

    def test_offline_stage_scripts_probe_the_network_first(self):
        # Stricter than the old index check: before the probe only our own mode
        # check, the argument/container guards and plain assignments may run.
        preamble = re.compile(r'(set -euo pipefail|factory=\$\(cd -- "\$\(dirname -- "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)'
                              r'|bash "\$factory/snitchwatch-system-mode\.sh"|\[\[ .*|echo \'[^\']*\' >&2; exit 1;|\}'
                              r'|(?:[a-z_]+=(?:\$\d|"\$factory/[\w.-]+")\s*)+)')
        for script in OFFLINE_SCRIPTS:
            lines = [line.strip() for line in (FACTORY / script).read_text().splitlines()[1:]
                     if line.strip() and not line.lstrip().startswith('#')]
            probe = lines.index('python3 "$factory/snitchwatch-system-handoff.py" probe-network offline'
                                if script != 'snitchwatch-system-assemble.sh' else 'python3 "$guard" probe-network offline')
            for line in lines[:probe]:
                self.assertRegex(line, r'\A' + preamble.pattern + r'\Z', script)
            for later in ('rpm -qa', 'cargo', 'build-bridge.sh', 'repro-check.sh', 'readarray', 'scanner-build.py'):
                if later in '\n'.join(lines):
                    self.assertGreater('\n'.join(lines).index(later), '\n'.join(lines).index('probe-network offline'), (script, later))
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" probe-network online', prep)

    def test_copy_from_handoffs_are_exactly_the_allowlisted_paths(self):
        allowed = {
            'snitchwatch-prep': ['--from=ctx /firewall/snitchwatch-system* /factory/'],
            'snitchwatch-system': ['--from=ctx /firewall/snitchwatch-system* /tools/',
                                   '--from=snitchwatch-scanner /handoff/scanner/ /handoff/scanner/',
                                   '--from=snitchwatch-bridge /handoff/bridge/ /handoff/bridge/',
                                   '--from=snitchwatch-daemon /handoff/daemon/ /handoff/daemon/'],
        }
        for name in BUILD_STAGES:
            copies = [rest for keyword, rest in self.stages[name]['body'] if keyword == 'COPY' and rest.startswith('--from=')]
            self.assertEqual(copies, allowed.get(name, []), name)
            for keyword, _ in self.stages[name]['body']:
                self.assertIn(keyword, ('ARG', 'COPY', 'RUN'), name)
        # Only the (scratch) build context and the final overlay are read by the final image.
        final = next(s for n, s in self.stages.items() if n.startswith('<final'))
        self.assertEqual(copy_sources(final), ['snitchwatch-selected'])

    def test_assembly_runs_tooling_only_from_ctx_and_the_rechecked_prep_checkout(self):
        run = runs(self.stages['snitchwatch-system'])[0]
        self.assertRegex(run, r' bash /tools/snitchwatch-system-assemble\.sh /work/native /handoff /out$')
        text = (FACTORY / 'snitchwatch-system-assemble.sh').read_text()
        body = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        executed = re.findall(r'(?:python3(?: -B)?|bash) "([^"]+)"', body)
        self.assertTrue(executed)
        for path in executed:
            self.assertTrue(path.startswith(('$factory/', '$guard', '$daemon_verifier', '$work/source/')), path)
        for needle in ('cargo', 'rustc', 'build-bridge', 'repro-check', '--run-flags', '--check-ldd', 'go build', 'dnf', 'curl', 'git '):
            self.assertNotIn(needle, body)
        # The handoffs are checked before anything reads them, and merged last.
        order = ['probe-network offline', 'cmp - "$work/rpm-inventory.txt"', "p['sourceFiles'].items()",
                 '"$guard" check scanner', '"$guard" check bridge', '"$guard" check daemon',
                 'bridge_artifact.py" verify', 'snitchwatch-system-stage.py"', '"$guard" check daemon', '"$guard" merge',
                 '"$daemon_verifier" --stage-only --root "$out"', 'snitchwatch-system-verify.py" --root "$out" --image-tree']
        position = 0
        for needle in order:  # each must appear after the previous one (check daemon twice)
            position = body.index(needle, position) + len(needle)
        # The daemon handoff is re-checked immediately before the merge (after 65534 ran).
        lines = [line for line in body.splitlines() if line.strip()]
        merge = next(i for i, line in enumerate(lines) if '"$guard" merge' in line)
        self.assertIn('"$guard" check daemon', lines[merge - 1])
        # /handoff itself must be root-owned and not group/other writable (renames inside it).
        self.assertIn('stat -c \'%u:%g:%a\' "$handoff"', body)
        self.assertLess(body.index('stat -c \'%u:%g:%a\' "$handoff"'), body.index('"$guard" check scanner'))

    def handoff_parent_check(self, stat_output):
        """Run the real /handoff owner/mode fragment of assemble.sh against a stubbed stat."""
        lines = (FACTORY / 'snitchwatch-system-assemble.sh').read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if 'stat -c \'%u:%g:%a\' "$handoff"' in line)
        end = next(i for i in range(start, len(lines)) if lines[i].startswith(('}', 'fi')))
        fragment = '\n'.join(lines[start:end + 1])
        script = 'set -euo pipefail\nhandoff=/handoff\nstat() { [[ "$*" == "-c %u:%g:%a /handoff" ]] && echo "$STAT_OUT"; }\n' + fragment + '\necho accepted\n'
        return subprocess.run(['bash', '-c', script], env={'PATH': '/usr/bin:/bin', 'STAT_OUT': stat_output},
                              capture_output=True, text=True)

    def test_handoff_parent_must_be_root_owned_without_group_or_other_write(self):
        for mode in ('755', '750', '700', '711', '551', '500'):
            with self.subTest(accepted=mode):
                result = self.handoff_parent_check('0:0:' + mode)
                self.assertEqual((result.returncode, result.stdout.strip()), (0, 'accepted'), result.stderr)
        for owner_mode in ('0:0:722', '0:0:733', '0:0:752', '0:0:753', '0:0:777', '0:0:775', '0:0:757',
                           '0:0:720', '0:0:702', '0:0:1755', '0:0:2755', '0:0:4755',
                           '1000:0:755', '0:1000:755', '65534:65534:755', ''):
            with self.subTest(refused=owner_mode):
                result = self.handoff_parent_check(owner_mode)
                self.assertNotEqual(result.returncode, 0, owner_mode)
                self.assertNotIn('accepted', result.stdout)
                self.assertIn('must be root-owned and not group/other writable', result.stderr)

    def test_every_consuming_stage_recomputes_the_shared_rpm_inventory(self):
        for script in ('snitchwatch-system-scanner-stage.sh', 'snitchwatch-system-bridge-build.sh', 'snitchwatch-system-assemble.sh'):
            text = (FACTORY / script).read_text()
            self.assertIn("rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\\n' | LC_ALL=C sort | cmp - \"$work/rpm-inventory.txt\"", text, script)
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertTrue(prep.rstrip().endswith('> "$work/rpm-inventory.txt"'))

    def test_prep_fetch_matches_the_bridge_build_fetch_and_runs_no_build(self):
        prep = (FACTORY / 'snitchwatch-system-prep.sh').read_text()
        self.assertIn('CARGO_HOME="$work/cargo" cargo fetch --locked --target x86_64-unknown-linux-gnu '
                      '--manifest-path "$work/source/Cargo.toml"', prep)
        for needle in ('cargo build', 'cargo test', 'cargo run', 'cargo install'):
            self.assertNotIn(needle, prep)

    def bridge_body(self):
        return '\n'.join(line for line in BRIDGE_SCRIPT.read_text().splitlines() if not line.lstrip().startswith('#'))

    def test_bridge_stage_runs_no_network_needing_command(self):
        body = self.bridge_body()
        for needle in ('dnf', 'curl', 'wget', 'git fetch', 'git clone', 'git submodule', 'rustup', 'pip ',
                       'SW_INSTALL_DEPS=1', 'install-deps', 'probe-network online', 'cargo install', 'cargo update'):
            self.assertNotIn(needle, body)
        self.assertIn('unset ALLOW_DIRTY SW_RELEASE_TAG SW_INSTALL_DEPS', body)
        # No snitchwatch build stage mounts anything (a cache or bind would bypass the netns argument).
        for name in BUILD_STAGES:
            for run in runs(self.stages[name]):
                self.assertNotIn('--mount', run, name)
        self.assertNotIn('--mount', body)

    def test_bridge_build_uses_an_offline_allowlisted_cargo_home(self):
        body = self.bridge_body()
        # Prep's cache is never the build's CARGO_HOME; the build gets a fresh one
        # holding only Cargo.lock-checksummed .crate files and the index.
        self.assertNotIn('CARGO_HOME="$work/cargo" ', body.replace('"$work/cargo" "$work/bridge-cargo"', ''))
        self.assertIn('python3 -B - "$factory/snitchwatch-system-scanner-build.py" "$work/cargo" "$work/bridge-cargo" '
                      '"$work/source/Cargo.lock" <<\'PY\'', body)
        export = next(line for line in body.splitlines() if line.startswith('export SRC_DIR='))
        self.assertIn('CARGO_HOME="$work/bridge-cargo"', export)
        self.assertIn('CARGO_NET_OFFLINE=true', export)
        order = ['probe-network offline', 'snitchwatch-system-scanner-build.py" "$work/cargo" "$work/bridge-cargo"',
                 'export SRC_DIR=', 'cargo fetch --locked --offline --target x86_64-unknown-linux-gnu --manifest-path "$SRC_DIR/Cargo.toml"',
                 '"$SRC_DIR/packaging/release/build-bridge.sh"', '"$SRC_DIR/packaging/release/repro-check.sh"',
                 '--check-ldd --run-flags', 'cmp - "$work/rpm-inventory.txt"', 'snitchwatch-system-handoff.py" export bridge']
        position = 0
        for needle in order:
            self.assertIn(needle, body[position:], needle)
            position = body.index(needle, position) + len(needle)
        self.assertIn('rm -rf -- "$CARGO_TARGET_DIR" "$work/bridge-cargo"', body)

    def bridge_fragment(self, first, last, trailing=0):
        lines = BRIDGE_SCRIPT.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(first))
        end = next(i for i in range(start, len(lines)) if last in lines[i]) + trailing
        return '\n'.join(lines[start:end + 1])

    def test_fresh_cargo_home_fragment_refuses_unpinned_or_altered_crates(self):
        import hashlib
        fragment = self.bridge_fragment('python3 -B - "$factory/snitchwatch-system-scanner-build.py"', 'bridge stage: prep cargo cache', trailing=1)
        self.assertTrue(fragment.endswith('\n}'), fragment)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = b'crate bytes'
            lock = '[[package]]\nname = "a"\nversion = "1.0.0"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "%s"\n' % hashlib.sha256(good).hexdigest()

            def attempt(label, cache_files, extra=()):
                work = root / label
                (work / 'source').mkdir(parents=True)
                (work / 'source/Cargo.lock').write_text(lock)
                for rel, data in {**cache_files, **dict(extra)}.items():
                    (work / 'cargo' / rel).parent.mkdir(parents=True, exist_ok=True)
                    (work / 'cargo' / rel).write_bytes(data)
                script = 'set -euo pipefail\nfactory=%s\nwork=%s\n%s\necho fresh-ok\n' % (FACTORY, work, fragment)
                return work, subprocess.run(['bash', '-c', script], env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
                                            capture_output=True, text=True)

            crate = {'registry/cache/index.crates.io-x/a-1.0.0.crate': good, 'registry/index/index.crates.io-x/.cache/a': b'idx'}
            work, result = attempt('ok', crate, {'config.toml': b'[build]\nrustc-wrapper="x"\n', 'bin/cargo-x': b'x',
                                                 'registry/src/index.crates.io-x/a-1.0.0/build.rs': b'evil'})
            self.assertEqual((result.returncode, result.stdout.strip()), (0, 'fresh-ok'), result.stderr)
            fresh = work / 'bridge-cargo'
            self.assertEqual(sorted(str(p.relative_to(fresh)) for p in fresh.rglob('*') if p.is_file()),
                             ['registry/cache/index.crates.io-x/a-1.0.0.crate', 'registry/index/index.crates.io-x/.cache/a'])
            for label, files in (('altered', {**crate, 'registry/cache/index.crates.io-x/a-1.0.0.crate': b'tampered'}),
                                 ('unpinned', {**crate, 'registry/cache/index.crates.io-x/b-2.0.0.crate': b'b'})):
                with self.subTest(refused=label):
                    work, result = attempt(label, files)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('fresh-ok', result.stdout)
                    self.assertIn('bridge stage: prep cargo cache failed the Cargo.lock checksum allowlist', result.stderr)

    def run_bridge_build_fragment(self, cargo_status):
        fragment = self.bridge_fragment('export SRC_DIR=', '"$SRC_DIR/packaging/release/build-bridge.sh"')
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, tmp)
        release = tmp / 'work/source/packaging/release'
        release.mkdir(parents=True)
        # The fake build runs a nested child the way repro-check.sh re-runs build-bridge.sh.
        (release / 'build-bridge.sh').write_text('#!/bin/bash\nbash -c \'echo "nested $CARGO_NET_OFFLINE $CARGO_HOME"\' >> "$EVENTS"\n')
        (release / 'build-bridge.sh').chmod(0o755)
        script = ('set -euo pipefail\nwork=%s\npin=(%s 1.2.3)\n'
                  'cargo() { echo "cargo $* offline=${CARGO_NET_OFFLINE:-unset} home=$CARGO_HOME" >> "$EVENTS"; return %d; }\n%s\n'
                  % (tmp / 'work', '0' * 40, cargo_status, fragment))
        events = tmp / 'events'
        result = subprocess.run(['bash', '-c', script], env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp), 'EVENTS': str(events)},
                                capture_output=True, text=True)
        return tmp / 'work', result, events.read_text().splitlines() if events.exists() else []

    def test_offline_env_reaches_nested_builds_after_a_passing_fetch_preflight(self):
        work, result, events = self.run_bridge_build_fragment(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        home = '%s/bridge-cargo' % work
        self.assertEqual(events, ['cargo fetch --locked --offline --target x86_64-unknown-linux-gnu --manifest-path %s/source/Cargo.toml '
                                  'offline=true home=%s' % (work, home), 'nested true %s' % home])

    def test_failed_offline_fetch_preflight_stops_before_the_build_with_a_distinct_message(self):
        _, result, events = self.run_bridge_build_fragment(101)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(events), 1, events)
        self.assertTrue(events[0].startswith('cargo fetch --locked --offline'))
        self.assertIn('bridge stage: offline cargo fetch preflight failed', result.stderr)

    def test_scanner_and_bridge_handoffs_are_exported_through_the_allowlist(self):
        scanner = (FACTORY / 'snitchwatch-system-scanner-stage.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" export scanner "$work/scanner" "$2"', scanner)
        bridge = (FACTORY / 'snitchwatch-system-bridge-build.sh').read_text()
        self.assertIn('snitchwatch-system-handoff.py" export bridge --pins "$pins" "$OUT_DIR" "$2"', bridge)
        # All bridge execution (ldd, --version, --help) happens in the bridge stage.
        self.assertIn('--check-ldd --run-flags', bridge)

    def test_old_single_run_factory_is_gone(self):
        self.assertFalse((FACTORY / 'snitchwatch-system-build.sh').exists())
        self.assertNotIn('snitchwatch-system-build.sh', CONTAINERFILE.read_text())


class WorkflowCacheSettings(unittest.TestCase):
    """Every check runs on every build: no CI system build may reuse cached layers."""

    def test_no_layer_cache_or_remote_cache_in_image_builds(self):
        checked = 0
        for workflow in sorted((ROOT / '.github/workflows').glob('*.yml')):
            text = workflow.read_text()
            for match in re.finditer(r'uses: redhat-actions/buildah-build@\S+.*?(?=\n\s*- name:|\n\S|\Z)', text, re.S):
                block = match.group(0)
                checked += 1
                self.assertIsNone(re.search(r'^\s*layers:\s*["\']?true', block, re.M), workflow.name)
                self.assertIsNone(re.search(r'--layers|--cache-from|--cache-to', block), workflow.name)
            for command in re.findall(r'podman build[^\n]*(?:\\\n[^\n]*)*', text):
                self.assertIsNone(re.search(r'--cache-from|--cache-to|--layers=true', command), workflow.name)
        # build.yml (verify + release), base-refresh.yml and r13-candidate.yml.
        self.assertGreaterEqual(checked, 4)

    def test_new_stage_tests_run_in_both_source_safety_blocks(self):
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(build.count('python3 tests/test-snitchwatch-image-stages.py'), 2)


class Handoff(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='sw-handoff.')
        self.addCleanup(work.cleanup)
        self.addCleanup(os.umask, os.umask(0o022))
        self.root = Path(work.name)
        self.h = runpy.run_path(str(HANDOFF))
        self.owner = (os.getuid(), os.getgid())

    def write(self, path, data=b'x', mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def scanner(self, root):
        for name, mode in self.h['allowlist']('scanner').items():
            self.write(root / name, name.encode(), mode)
        return root

    def check(self, kind, root, **extra):
        return self.h['check'](kind, root, PINS, owner=self.owner, **extra)

    def refused(self, kind, root, message, **extra):
        with self.assertRaises(self.h['Refusal']) as caught:
            self.check(kind, root, **extra)
        self.assertIn(message, str(caught.exception))

    def test_scanner_allowlist_matches_what_stage_py_and_the_verifier_consume(self):
        allowed = self.h['allowlist']('scanner')
        stage = (FACTORY / 'snitchwatch-system-stage.py').read_text()
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        evidence = set(verifier['SCANNER_EVIDENCE']) | {'LICENSE', 'THIRD-PARTY-LICENSES.md'}
        binaries = {Path(name).name for name in verifier['SCANNER_BINARIES']}
        self.assertEqual(set(allowed), evidence | binaries | {'provenance.json'})
        self.assertEqual({name for name, mode in allowed.items() if mode == 0o755}, binaries)
        self.assertIn("scanner / 'provenance.json'", stage)
        # Flat: no source export, cargo home or target directory crosses the boundary.
        self.assertFalse(any('/' in name or name.startswith(('source.', 'cargo', 'target')) for name in allowed))

    def test_bridge_allowlist_is_tarball_sidecar_and_logs(self):
        tarball = 'snitchwatch-bridge-%s-x86_64.tar.gz' % PINS['bridgeVersion']
        self.assertEqual(set(self.h['allowlist']('bridge', PINS)), {tarball, tarball + '.sha256', 'build.log', 'repro.log'})

    def test_daemon_allowlist_is_the_trusted_verifier_inventory(self):
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-daemon-verify.py'))
        allowed = self.h['allowlist']('daemon', daemon_verifier=FACTORY / 'snitchwatch-system-daemon-verify.py')
        self.assertEqual(set(allowed), {name.lstrip('/') for name in set(verifier['REQUIRED']) | {verifier['MANIFEST']}})
        self.assertEqual(allowed['usr/share/snitchwatch/daemon/opensnitchd'], 0o755)
        self.assertEqual(allowed['usr/libexec/snitchwatch/verify-system-daemon.py'], 0o755)
        with self.assertRaises(self.h['Refusal']):
            self.h['allowlist']('daemon')

    def test_valid_handoff_passes_and_export_copies_only_the_allowlist(self):
        source = self.scanner(self.root / 'scanner-out')
        for extra in ('source/packaging/release/bridge_artifact.py', 'cargo-home/registry/x.crate', 'target-a/release/x'):
            self.write(source / extra)
        dest = self.root / 'handoff/scanner'
        self.h['export']('scanner', source, dest)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(self.h['allowlist']('scanner')))
        self.assertEqual(len(self.check('scanner', dest)), 12)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o755)
        with self.assertRaises(self.h['Refusal']):
            self.h['export']('scanner', source, dest)

    def test_daemon_export_is_byte_and_mode_identical_and_extras_fail_the_check(self):
        verifier = FACTORY / 'snitchwatch-system-daemon-verify.py'
        allowed = self.h['allowlist']('daemon', daemon_verifier=verifier)
        source = self.root / 'daemon-out'
        for name, mode in allowed.items():
            self.write(source / name, name.encode(), mode)
        self.assertEqual(len(self.h['check']('daemon', source, daemon_verifier=verifier, owner=self.owner)), len(allowed))
        dest = self.root / 'handoff/daemon'
        self.h['export']('daemon', source, dest, daemon_verifier=verifier)
        listing = lambda root: sorted((str(p.relative_to(root)), stat.S_IMODE(p.stat().st_mode), p.read_bytes() if p.is_file() else b'')
                                      for p in root.rglob('*'))
        self.assertEqual(listing(source), listing(dest))
        self.write(source / 'usr/share/snitchwatch/daemon/extra.so')
        with self.assertRaisesRegex(self.h['Refusal'], 'unexpected file in daemon handoff'):
            self.h['check']('daemon', source, daemon_verifier=verifier, owner=self.owner)

    def test_export_refuses_a_symlinked_source_file(self):
        source = self.scanner(self.root / 'out')
        (source / 'build.log').unlink()
        (source / 'build.log').symlink_to('/etc/hostname')
        with self.assertRaises(self.h['Refusal']):
            self.h['export']('scanner', source, self.root / 'dest')

    def test_check_refuses_every_unexpected_shape(self):
        cases = {
            'extra file': (lambda r: self.write(r / 'evil.so'), 'unexpected file'),
            'source tree': (lambda r: self.write(r / 'source/packaging/system/stage.sh'), 'unexpected directory'),
            'missing file': (lambda r: (r / 'test.log').unlink(), 'missing: test.log'),
            'symlink': (lambda r: ((r / 'LICENSE').unlink(), (r / 'LICENSE').symlink_to('/etc/passwd')), 'symlink or special'),
            'dir symlink': (lambda r: (r / 'sub').symlink_to('/'), 'symlink or special'),
            'fifo': (lambda r: os.mkfifo(r / 'pipe'), 'symlink or special'),
            'setuid': (lambda r: (r / 'snitchwatch-scanner-broker').chmod(0o4755), 'setuid'),
            'setgid dir': (lambda r: r.chmod(0o2755), 'setuid'),
            'wrong mode': (lambda r: (r / 'provenance.json').chmod(0o755), 'has mode 755, want 644'),
            'writable dir': (lambda r: r.chmod(0o777), 'group/other writable'),
            'hard link': (lambda r: os.link(r / 'build.log', self.root / ('link-' + r.name)), 'hard-linked'),
        }
        for label, (mutate, message) in cases.items():
            with self.subTest(label):
                root = self.scanner(self.root / label.replace(' ', '-'))
                mutate(root)
                self.refused('scanner', root, message)

    def test_check_refuses_wrong_owner_capabilities_and_acls(self):
        root = self.scanner(self.root / 'owner')
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['check']('scanner', root, owner=(self.owner[0], self.owner[1] + 1))
        self.assertIn('is owned by', str(caught.exception))
        for attribute in ('security.capability', 'system.posix_acl_access'):
            with self.subTest(attribute), mock.patch('os.listxattr', return_value=[attribute]):
                self.refused('scanner', root, 'refused extended attribute ' + attribute)
        with mock.patch('os.listxattr', return_value=['security.selinux']):
            self.assertEqual(len(self.check('scanner', root)), 12)
        with self.assertRaises(OSError):
            self.check('scanner', self.root / 'absent')

    def test_xattr_listing_errors_fail_closed(self):
        import errno
        root = self.scanner(self.root / 'xattr')
        with mock.patch('os.listxattr', side_effect=PermissionError(errno.EACCES, 'denied')):
            self.refused('scanner', root, 'cannot list extended attributes')
        # A filesystem without xattr support cannot carry capabilities or ACLs.
        with mock.patch('os.listxattr', side_effect=OSError(errno.ENOTSUP, 'not supported')):
            self.assertEqual(len(self.check('scanner', root)), 12)

    def test_merge_adds_the_overlay_and_refuses_any_overlap(self):
        overlay = self.root / 'daemon'
        self.write(overlay / 'usr/share/snitchwatch/daemon/opensnitchd', b'elf', 0o755)
        self.write(overlay / 'usr/share/licenses/snitchwatch-opensnitchd/LICENSE', b'gpl')
        out = self.root / 'out'
        self.write(out / 'usr/share/snitchwatch/system-bridge-manifest.json', b'{}')
        added = self.h['merge'](overlay, out)
        self.assertEqual(sorted(added), ['usr/share/licenses/snitchwatch-opensnitchd/LICENSE', 'usr/share/snitchwatch/daemon/opensnitchd'])
        self.assertEqual(stat.S_IMODE((out / 'usr/share/snitchwatch/daemon/opensnitchd').stat().st_mode), 0o755)
        self.assertEqual((out / 'usr/share/snitchwatch/system-bridge-manifest.json').read_bytes(), b'{}')
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['merge'](overlay, out)
        self.assertIn('merge overlap', str(caught.exception))
        # A file where the overlay needs a directory, and a symlinked directory, are overlaps too.
        for label, prepare in (('file-for-dir', lambda o: self.write(o / 'usr/share/snitchwatch')),
                               ('symlink-dir', lambda o: (o / 'usr/share').mkdir(parents=True) or (o / 'usr/share/snitchwatch').symlink_to(self.root))):
            with self.subTest(label):
                target = self.root / label
                prepare(target)
                with self.assertRaises(self.h['Refusal']):
                    self.h['merge'](overlay, target)

    def test_cli_reports_a_distinct_refusal_line(self):
        root = self.scanner(self.root / 'cli')
        self.write(root / 'evil')
        result = subprocess.run(['python3', str(HANDOFF), 'check', 'scanner', str(root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r'^handoff: check: ')


class NetworkProbe(unittest.TestCase):
    def setUp(self):
        self.h = runpy.run_path(str(HANDOFF))
        work = tempfile.TemporaryDirectory(prefix='sw-probe.')
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)

    def dev(self, *names):
        path = self.root / 'dev'
        path.write_text('Inter-|   Receive\n face |bytes\n' + ''.join('%6s: 0 0 0\n' % name for name in names))
        return path

    @staticmethod
    def refuse(*_args, **_kwargs):
        raise OSError(101, 'Network is unreachable')

    @staticmethod
    def accept(*_args, **_kwargs):
        return mock.Mock()

    def resolve_ok(self, host, port, **_kwargs):
        return [(None, None, None, None, ('192.0.2.1', port))]

    def test_offline_passes_only_with_lo_and_every_attempt_failing(self):
        result = self.h['probe_network']('offline', self.dev('lo'), self.refuse, self.refuse)
        self.assertEqual(result['interfaces'], ['lo'])
        self.assertTrue(all(value != 'connected' for value in result['results'].values()))

    def test_offline_refuses_extra_interface(self):
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo', 'eth0'), self.refuse, self.refuse)
        self.assertIn('RUN --network=none is not in effect', str(caught.exception))

    def test_offline_refuses_any_successful_connection(self):
        # e.g. a builder that leaves a routable lo-only namespace or ignores the flag
        calls = iter([self.refuse, self.accept])
        connect = lambda *a, **k: next(calls)(*a, **k)
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo'), connect, self.refuse)
        self.assertIn('outbound connection succeeded', str(caught.exception))
        # A name that resolves locally is not reachability; connecting to it is.
        result = self.h['probe_network']('offline', self.dev('lo'), self.refuse, self.resolve_ok)
        self.assertIn('resolved', result['results']['dns registry.fedoraproject.org'])
        calls = iter([self.refuse, self.refuse, self.accept])
        with self.assertRaises(self.h['Refusal']) as caught:
            self.h['probe_network']('offline', self.dev('lo'), lambda *a, **k: next(calls)(*a, **k), self.resolve_ok)
        self.assertIn('registry.fedoraproject.org:443 (192.0.2.1)', str(caught.exception))

    def test_online_positive_control(self):
        self.h['probe_network']('online', self.dev('lo', 'eth0'), self.accept, self.resolve_ok)
        for resolve in (self.refuse, self.resolve_ok):  # resolving alone is not a pass
            with self.subTest(resolve=resolve), self.assertRaises(self.h['Refusal']) as caught:
                self.h['probe_network']('online', self.dev('lo', 'eth0'), self.refuse, resolve)
            self.assertIn('positive control failed', str(caught.exception))

    def test_probe_targets_are_a_literal_address_and_a_name(self):
        self.assertTrue(all(re.fullmatch(r'[0-9.]+|[0-9a-f:]+', host) for host, _ in self.h['PROBE_ADDRESSES']))
        self.assertFalse(re.fullmatch(r'[0-9.:]+', self.h['PROBE_NAME'][0]))

    def test_interfaces_reads_this_netns(self):
        self.assertEqual(self.h['interfaces'](self.dev('lo', 'eth0')), ['eth0', 'lo'])


class UnprivilegedBridgeExecution(unittest.TestCase):
    """stage.py never runs the bridge binary (built by ~660 crates) as root."""

    def setUp(self):
        self.g = runpy.run_path(str(FACTORY / 'snitchwatch-system-stage.py'))
        self.real = self.g['main'].__globals__  # the functions' live globals
        work = tempfile.TemporaryDirectory(prefix='sw-unpriv.')
        self.addCleanup(work.cleanup)
        self.addCleanup(os.umask, os.umask(0o022))
        self.root = Path(work.name)

    def test_root_drops_to_65534_with_an_empty_environment(self):
        with mock.patch('os.geteuid', return_value=0):
            options = self.g['unprivileged_options']()
        self.assertEqual((options['user'], options['group'], options['extra_groups']), (65534, 65534, []))
        self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        self.assertEqual((options['cwd'], options['umask']), ('/', 0o022))
        with mock.patch('os.geteuid', return_value=1000):
            self.assertNotIn('user', self.g['unprivileged_options']())

    def test_unprivileged_child_has_no_new_privs(self):
        # Real child: the kernel must report NoNewPrivs 1 for the process that runs the binary.
        result = self.g['run_unprivileged'](['cat', '/proc/self/status'], capture_output=True, text=True, timeout=10)
        self.assertIn('NoNewPrivs:\t1', result.stdout)
        self.assertIn('preexec_fn', self.g['unprivileged_options']())

    def test_no_new_privs_failure_fails_closed(self):
        self.assertEqual(self.real['PR_SET_NO_NEW_PRIVS'], 38)
        with mock.patch.dict(self.real, {'prctl': lambda *a: -1}):
            with self.assertRaisesRegex(ValueError, 'no_new_privs'):
                self.g['run_unprivileged'](['true'], timeout=10)

    def test_stage_sh_run_has_a_distinct_timeout(self):
        self.assertEqual(self.real['STAGE_SH_TIMEOUT'], 300)
        text = (FACTORY / 'snitchwatch-system-stage.py').read_text()
        self.assertIn('timeout=STAGE_SH_TIMEOUT', text)
        # subprocess reports a measured, remaining-time float (0.99996 on a slow runner,
        # CI run 40810566230); the message must carry the configured value, exactly.
        for measured in (0.9999658950000025, 1.0000001, 299.99):
            for configured, command, name in ((1, ['sleep', '5'], 'sleep'), (300, ['bash', '/src/packaging/system/stage.sh', 'x'], 'stage.sh')):
                with self.subTest(measured=measured, configured=configured), \
                        mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired(command, measured, output=b'partial')):
                    with self.assertRaises(ValueError) as caught:
                        self.g['run_unprivileged'](command, timeout=configured)
                    self.assertEqual(str(caught.exception), '%s timed out after %d s running the bridge binary' % (name, configured))
                    self.assertNotIn('partial', str(caught.exception))

    def test_timed_out_child_is_killed_and_its_output_discarded(self):
        # Real process, no timing assertion: 'exec sleep' keeps one process holding the pipe,
        # which subprocess must kill; the call raises, so no partial output is ever returned.
        marker = self.root / 'child.pid'
        command = ['sh', '-c', 'echo partial; echo $$ > %s; exec sleep 60' % marker]
        with self.assertRaisesRegex(ValueError, '^sh timed out after 1 s running the bridge binary$'):
            self.g['run_unprivileged'](command, timeout=1, capture_output=True, text=True)
        pid = int(marker.read_text())
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # already reaped by subprocess: it was killed, not left running
        self.assertFalse(Path('/proc/%d' % pid).exists() and 'sleep' in Path('/proc/%d/cmdline' % pid).read_text(errors='replace'))

    def test_both_bridge_executions_go_through_run_unprivileged(self):
        text = (FACTORY / 'snitchwatch-system-stage.py').read_text()
        self.assertIn("run_unprivileged([str(binary), '--version']", text)
        self.assertIn("run_unprivileged(['bash', str(source / 'packaging/system/stage.sh')", text)
        self.assertNotIn("check_output([str(binary)", text)
        self.assertEqual(text.count("'packaging/system/stage.sh'"), 1)

    def test_root_run_kills_leftover_processes_and_maps_permission_errors(self):
        calls = []
        fake = mock.Mock(side_effect=lambda *a, **k: calls.append(k) or mock.Mock(stdout='x'))
        with mock.patch.dict(self.real, {'kill_leftover_children': lambda: calls.append('killed'),
                                         'become_subreaper': lambda: calls.append('subreaper')}), \
                mock.patch('os.geteuid', return_value=0), mock.patch('subprocess.run', fake):
            self.g['run_unprivileged'](['true'])
        self.assertEqual(calls[0], 'subreaper')
        self.assertEqual(calls[1]['user'], 65534)
        self.assertEqual(calls[-1], 'killed')
        with mock.patch.dict(self.real, {'kill_leftover_children': lambda: None, 'become_subreaper': lambda: None}), \
                mock.patch('os.geteuid', return_value=0), mock.patch('subprocess.run', side_effect=PermissionError('setgroups')):
            with self.assertRaisesRegex(ValueError, 'cannot drop to uid/gid 65534'):
                self.g['run_unprivileged'](['true'])

    def test_double_forked_descendant_is_reparented_and_killed(self):
        # A real orphan (fork, setsid, fork, sleep) in a child process acting as the stage runner.
        code = r"""
import os, runpy, subprocess, sys, time
g = runpy.run_path(sys.argv[1])
g['become_subreaper']()
# The inner shell prints the orphan's pid only after forking it; wait on that
# condition (reparenting completes during the shell's exit), not on a fixed delay.
out = subprocess.run(['sh', '-c', 'setsid sh -c "sleep 300 >/dev/null 2>&1 & echo \\$!"'], check=True, capture_output=True, text=True).stdout
orphan = int(out.split()[0])
deadline = time.monotonic() + 60
while orphan not in g['children'](os.getpid()):
    if time.monotonic() > deadline:
        raise SystemExit('orphan %d was never reparented to the subreaper' % orphan)
    time.sleep(0.01)
before = len(g['children'](os.getpid()))
g['kill_leftover_children']()
print(before, len(g['children'](os.getpid())))
"""
        result = subprocess.run(['python3', '-c', code, str(FACTORY / 'snitchwatch-system-stage.py')], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        before, after = map(int, result.stdout.split())
        self.assertGreaterEqual(before, 1)
        self.assertEqual(after, 0)

    def test_executable_and_parents_must_not_be_writable_by_the_unprivileged_user(self):
        binary = self.root / 'a/usr/bin/snitchwatch-bridge-cli'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        require = self.g['require_not_writable_by']
        require(binary, None)
        for target, mode in ((binary, 0o775), (binary.parent, 0o777), (binary, 0o757)):
            with self.subTest(str(target), mode=oct(mode)):
                target.chmod(mode)
                with self.assertRaises(ValueError):
                    require(binary, None)
                target.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'writable by uid'):
            require(binary, os.getuid())
        (self.root / 'link').symlink_to(binary)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            require(self.root / 'link', None)

    def stage_tree(self, root, files):
        for name, mode in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            path.chmod(mode)
        return root

    def test_adopt_copies_exactly_the_stage_sh_file_set(self):
        files = self.g['SYSTEM_STAGE_FILES']
        self.assertEqual(len(files), 9)
        verifier = runpy.run_path(str(FACTORY / 'snitchwatch-system-verify.py'))
        expected = {name.lstrip('/') for name, origin in verifier['SOURCE_FILES'].items() if origin.startswith(('packaging/system/', 'packaging/bluebuild/'))}
        self.assertEqual(set(files) - expected, {'usr/bin/snitchwatch-bridge-cli', 'etc/opensnitchd/default-config.json'})
        staged = self.stage_tree(self.root / 'staged', files)
        out = self.root / 'out'
        self.g['adopt_unprivileged_stage'](staged, out)
        for name, mode in files.items():
            self.assertEqual((out / name).read_bytes(), name.encode())
            self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), mode)
            self.assertEqual((out / name).stat().st_uid, os.geteuid())
        dirs = {str(p.relative_to(out)) for p in out.rglob('*') if p.is_dir()}
        self.assertTrue(all(stat.S_IMODE((out / d).stat().st_mode) == 0o755 for d in dirs))

    def test_adopt_refuses_anything_the_binary_could_have_planted(self):
        files = self.g['SYSTEM_STAGE_FILES']
        cases = {
            'extra unit': lambda s: (s / 'etc/systemd/system').mkdir(parents=True),
            'extra file': lambda s: (s / 'usr/lib/systemd/system/evil.service').write_text('x'),
            'symlink': lambda s: ((s / 'usr/lib/tmpfiles.d/snitchwatch.conf').unlink(), (s / 'usr/lib/tmpfiles.d/snitchwatch.conf').symlink_to('/etc/shadow')),
            'setuid': lambda s: (s / 'usr/bin/snitchwatch-bridge-cli').chmod(0o4755),
            'wrong mode': lambda s: (s / 'etc/opensnitchd/default-config.json').chmod(0o600),
            'hard link': lambda s: os.link(s / 'usr/lib/sysusers.d/snitchwatch.conf', self.root / ('hl-' + s.name)),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                staged = self.stage_tree(self.root / ('s-' + label.replace(' ', '-')), files)
                mutate(staged)
                with self.assertRaisesRegex(ValueError, 'unexpected path in unprivileged stage.sh output'):
                    self.g['adopt_unprivileged_stage'](staged, self.root / ('o-' + label.replace(' ', '-')))
        real = self.stage_tree(self.root / 'real-root', files)
        (self.root / 'linked-root').symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'staging root is not a real directory'):
            self.g['adopt_unprivileged_stage'](self.root / 'linked-root', self.root / 'o-linked')


if __name__ == '__main__':
    unittest.main()
