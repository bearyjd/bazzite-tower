#!/usr/bin/env python3
"""Build and test the scanner from the pinned upstream tree in a disposable native builder.

Never invoke the installed privileged entrypoint, pkexec, or a live scan.
The caller supplies the already verified clean source and pinned toolchain.
Two phases (M1 step 3): `--phase inputs` as root (export, pin checks, cargo home,
toolchain digest), then `--phase build` as uid 65534 through
snitchwatch-system-unpriv.py (every cargo, rustc, test and ldd run).
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib

BINARIES = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
PACKAGES = ('scanner-privileged', 'scanner-broker')
BUILD = ['cargo', 'build', '--release', '--locked', '--offline', '-p', PACKAGES[0], '-p', PACKAGES[1]]
HELPER = 'packaging/release/bridge_artifact.py'
# Only these variables reach cargo/rustc. Everything else is dropped, and any
# variable that could steer the compiler, linker, C toolchain or cargo config is
# refused outright so a polluted builder fails instead of building quietly.
ENV_KEEP = ('PATH', 'HOME', 'CARGO_HOME', 'CARGO_TARGET_DIR', 'CARGO_BUILD_JOBS', 'RUSTUP_HOME', 'TMPDIR', 'SOURCE_DATE_EPOCH')
ENV_REFUSE = re.compile(
    r'^(CC|CXX|CPP|AR|LD|NM|RANLIB|STRIP|OBJCOPY|PKG_CONFIG\w*|(CC|CXX|CFLAGS|CXXFLAGS|CPPFLAGS|LDFLAGS|AR)_\w+'
    r'|C(XX|PP)?FLAGS|LDFLAGS|LD_\w+|RUST\w*|CARGO_\w+|SSL_CERT\w*|PYTHON\w*)$')
# Set by the builder itself below, so an inherited value is overwritten rather than refused.
ENV_SET_BY_BUILDER = ('PYTHONDONTWRITEBYTECODE',)


def clean_environment(environment):
    """Allowlisted child environment; refuse unexpected toolchain-steering variables."""
    for key in sorted(environment):
        if key not in ENV_KEEP and key not in ENV_SET_BY_BUILDER and key != 'LANG' and ENV_REFUSE.match(key):
            raise ValueError('refusing build-steering environment variable: ' + key)
    # Children must never write bytecode caches into the pinned checkouts.
    return {**{key: environment[key] for key in ENV_KEEP if key in environment}, 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}


def toolchain_digest(paths):
    """Order-independent digest of file contents (symlinks: their target text) at the given paths."""
    digest = hashlib.sha256()
    for path in sorted(str(p) for p in paths):
        if os.path.islink(path):
            entry = 'link:' + os.readlink(path)
        elif os.path.isfile(path):
            entry = 'file:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        elif os.path.lexists(path):
            continue  # directories and special files carry no content
        else:
            entry = 'missing'
        digest.update(('%s\0%s\n' % (path, entry)).encode())
    return digest.hexdigest()


def installed_toolchain_digest(pins):
    """Digest of the files installed by the pinned rust, cargo and rust-std-static RPMs only (rpm -ql).

    A partial fingerprint: it does not cover cc, ld, the linker/libc, PATH or /etc/ld.so.preload."""
    paths = []
    for rpm in pins['toolchainRpms']:
        try:
            listing = subprocess.check_output(['rpm', '-ql', rpm['name']], text=True)
        except (subprocess.CalledProcessError, OSError) as error:
            raise ValueError('cannot list files of pinned toolchain package %s (rpm -ql): %s' % (rpm['name'], error)) from error
        paths += [line for line in listing.splitlines() if line.startswith('/')]
    if not paths:
        raise ValueError('pinned toolchain lists no files')
    return toolchain_digest(paths)


def require_toolchain_digest(record, actual):
    if not record.is_file():
        raise ValueError('toolchain digest was not recorded after install: ' + str(record))
    if record.read_text().strip() != actual:
        raise ValueError('toolchain changed since install (digest mismatch); refusing')


def require_no_cargo_config(exported, cargo_home):
    """Cargo merges config from the build dir, every ancestor and CARGO_HOME; none may exist."""
    found = [str(base / name) for base in (*(d / '.cargo' for d in (exported, *exported.parents)), cargo_home)
             for name in ('config', 'config.toml') if os.path.lexists(base / name)]
    if found:
        raise ValueError('cargo config would influence the scanner build: ' + ', '.join(found))


def make_fresh_cargo_home(bridge_home, fresh, lock_text):
    """Rebuild CARGO_HOME from checksum-verified .crate files; registry/src, config and bin are not carried over (hardening, not isolation)."""
    wanted = {'%s-%s.crate' % (p['name'], p['version']): p['checksum']
              for p in tomllib.loads(lock_text).get('package', ()) if 'checksum' in p}
    cache = bridge_home / 'registry' / 'cache'
    if not cache.is_dir():
        raise ValueError('bridge cargo cache missing: ' + str(cache))
    copies = []
    for path in sorted(cache.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('non-regular cargo cache entry: ' + str(path))
        if path.is_file():
            if wanted.get(path.name) is None:
                raise ValueError('cached file not in Cargo.lock: ' + str(path.relative_to(cache)))
            if sha(path) != wanted[path.name]:
                raise ValueError('cached crate differs from Cargo.lock checksum: ' + path.name)
            copies.append(path)
    if not copies:
        raise ValueError('bridge cargo cache holds no crates')
    index = bridge_home / 'registry' / 'index'
    for path in sorted(index.rglob('*')) if index.is_dir() else ():
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('non-regular cargo index entry: ' + str(path))
    fresh.mkdir(parents=True)
    for path in copies:
        destination = fresh / 'registry' / 'cache' / path.relative_to(cache)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    if index.is_dir():
        shutil.copytree(index, fresh / 'registry' / 'index', symlinks=False)
    return fresh


def load_bridge_artifact(source):
    """Load the bridge helper from the clean checkout without writing __pycache__ into it."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('artifact', source / 'packaging/release/bridge_artifact.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_pinned_scanner_sources(exported, pins):
    """Every file in the built scanner crates must be pinned, present and hash to its pin."""
    prefixes = tuple('crates/%s/' % package for package in PACKAGES)
    pinned = {name: expected for name, expected in pins['sourceFiles'].items() if name.startswith('crates/scanner-')}
    for package in PACKAGES:
        crate = exported / 'crates' / package
        for path in sorted(crate.rglob('*')) if crate.is_dir() else ():
            name = str(path.relative_to(exported))
            if path.is_file() and name not in pinned:
                raise ValueError('unpinned scanner input: ' + name)
    for name, expected in pinned.items():
        if not (exported / name).is_file():
            raise ValueError('missing pinned scanner input: ' + name)
        if sha(exported / name) != expected:
            raise ValueError('scanner input differs from pinned upstream: ' + name)
    if not any(name.startswith(prefixes) for name in pinned):
        raise ValueError('no scanner crate files are pinned')


def require_pinned_file(exported, pins, name):
    """A file of the export that the builder trusts (Cargo.lock, the bridge helper) must match its pin."""
    expected = pins['sourceFiles'].get(name)
    if expected is None or not (exported / name).is_file() or sha(exported / name) != expected:
        raise ValueError('file differs from pinned upstream: ' + name)


def require_bridge_cargo_home(value):
    """CARGO_HOME of the bridge build must be set and an existing directory (never Path('') == '.')."""
    if not value or not Path(value).is_dir():
        raise ValueError('CARGO_HOME of the bridge build is unset or missing')
    return Path(value)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def require_private_bus_test(log_text):
    """The ignored private-bus test must really have run: exactly one passed, none failed."""
    passed = [int(count) for count in re.findall(r'test result: ok\. (\d+) passed', log_text)]
    if 'test result: FAILED' in log_text or sum(passed) != 1:
        raise ValueError('scanner private-bus test did not run exactly one passing test')


def run(command, cwd, env, log):
    with log.open('ab') as output:
        subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)


def require_builder_container():
    if not (Path('/run/.containerenv').exists() or Path('/.dockerenv').exists()) or Path('/run/host').exists():
        raise ValueError('scanner build requires the fresh native builder')


def remap(exported, cargo_home, target):
    return f'--remap-path-prefix={exported}=/snitchwatch --remap-path-prefix={cargo_home}=/cargo --remap-path-prefix={target}=/target'


def inputs_phase(args):
    """Root, before any third-party code (M1 step 3): export, pin-check, allowlisted cargo home, toolchain digest.

    Writes only the new root-owned INPUTS (the export and two json records) and CARGO_HOME;
    runs git and rpm but never cargo, rustc, a build script or anything built."""
    require_builder_container()
    os.umask(0o022)  # the unprivileged build must be able to read what this phase writes, and not write it
    source = args.source.resolve()
    inputs, cargo_home = args.inputs.resolve(), args.cargo_home.resolve()
    if os.path.lexists(inputs) or os.path.lexists(cargo_home) or Path('/') in (inputs, cargo_home):
        raise ValueError('scanner inputs and cargo home must be new isolated directories')
    pins = json.loads(args.pins.read_text())
    bridge_cargo_home = require_bridge_cargo_home(os.environ.get('CARGO_HOME'))
    inputs.mkdir()
    exported = inputs / 'source'
    exported.mkdir()
    archive = inputs / 'source.tar'
    with archive.open('wb') as output:
        subprocess.run(['git', '-C', str(source), 'archive', pins['sourceCommit']], stdout=output, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(exported, filter='data')
    archive.unlink()
    require_pinned_scanner_sources(exported, pins)
    require_pinned_file(exported, pins, HELPER)
    require_pinned_file(exported, pins, 'Cargo.lock')
    write_json(inputs / 'source-inputs.json', {str(path.relative_to(exported)): sha(path)
               for path in sorted(exported.rglob('*')) if path.is_file()})
    # This runs in the snitchwatch-scanner stage (RUN --network=none, FROM
    # snitchwatch-prep), whose ancestry never ran the bridge build scripts;
    # CARGO_HOME is prep's `cargo fetch --locked` cache (downloads only).
    # Defence in depth on top of that stage isolation: reuse only the
    # checksum-verified .crate files (Cargo.lock is pin-checked above); cargo
    # re-extracts them; no cargo config may exist where it looks.
    make_fresh_cargo_home(bridge_cargo_home, cargo_home, (exported / 'Cargo.lock').read_text())
    require_no_cargo_config(exported, cargo_home)
    require_toolchain_digest(args.toolchain_digest, installed_toolchain_digest(pins))
    epoch = subprocess.check_output(['git', '-C', str(source), 'log', '-1', '--format=%ct'], text=True).strip()
    if not epoch.isdigit():
        raise ValueError('source commit time is not a number: %r' % epoch)
    write_json(inputs / 'build-inputs.json', {'sourceDateEpoch': epoch})
    print('scanner inputs ready (root); the build runs as an unprivileged uid', flush=True)


def build_phase(args):
    """Unprivileged (behind snitchwatch-system-unpriv.py, uid 65534): every cargo, rustc, test, ldd run.

    Reads the root-owned INPUTS and writes only CARGO_HOME (cargo re-extracts crates there) and OUT."""
    if os.geteuid() == 0:
        raise ValueError('scanner build phase: refusing to run as root (it runs only behind snitchwatch-system-unpriv.py)')
    require_builder_container()
    inputs, cargo_home, out = args.inputs.resolve(), args.cargo_home.resolve(), args.out.resolve()
    if not out.is_dir() or out.is_symlink() or any(out.iterdir()):
        raise ValueError('output must be the empty scratch directory prepared for the build uid: ' + str(out))
    pins = json.loads(args.pins.read_text())
    exported = inputs / 'source'
    require_no_cargo_config(exported, cargo_home)
    env = clean_environment(os.environ)
    env['CARGO_HOME'] = str(cargo_home)
    env['SOURCE_DATE_EPOCH'] = json.loads((inputs / 'build-inputs.json').read_text())['sourceDateEpoch']
    env['CARGO_INCREMENTAL'] = '0'
    env['CARGO_NET_OFFLINE'] = 'true'
    shutil.copyfile(inputs / 'source-inputs.json', out / 'source-inputs.json')
    # Each build gets an empty target and identical remapped source/cargo/target paths.
    targets = [out / 'target-a', out / 'target-b']
    for index, target in enumerate(targets):
        env['CARGO_TARGET_DIR'] = str(target)
        env['RUSTFLAGS'] = remap(exported, cargo_home, target)
        run(BUILD, exported, env, out / ('build.log' if index == 0 else 'repro.log'))
    for binary in BINARIES:
        if sha(targets[0] / 'release' / binary) != sha(targets[1] / 'release' / binary):
            raise ValueError('scanner executable is not reproducible: ' + binary)
        destination = out / binary
        shutil.copyfile(targets[0] / 'release' / binary, destination)
        destination.chmod(0o755)
        result = subprocess.run(['ldd', str(destination)], capture_output=True, text=True, check=True)
        if 'not found' in result.stdout + result.stderr:
            raise ValueError('scanner dynamic dependency missing: ' + binary)
        (out / (binary + '.ldd')).write_text(result.stdout)
    env['CARGO_TARGET_DIR'] = str(targets[0])
    env['RUSTFLAGS'] = remap(exported, cargo_home, targets[0])
    run(['cargo', 'test', '--locked', '--offline', '-p', PACKAGES[0], '-p', PACKAGES[1], '--', '--test-threads=1'], exported, env, out / 'test.log')
    bus_log = out / 'private-bus.log'
    run(['dbus-run-session', '--', 'cargo', 'test', '--locked', '--offline', '-p', PACKAGES[1], 'private_bus', '--', '--ignored', '--test-threads=1'], exported, env, bus_log)
    require_private_bus_test(bus_log.read_text(errors='replace'))
    with (out / 'test.log').open('ab') as combined:
        combined.write(bus_log.read_bytes())
    bus_log.unlink()
    # Resolve each executable's own normal+build graph, rather than borrowing
    # the bridge's license closure. Include the actual license/notice texts.
    artifact = load_bridge_artifact(exported)
    metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--locked', '--offline', '--format-version', '1', '--filter-platform', 'x86_64-unknown-linux-gnu'], cwd=exported, env=env, text=True))
    members = set(metadata['workspace_members'])
    closure = {}
    selected = {}
    for package, binary in zip(PACKAGES, BINARIES):
        tree = subprocess.check_output(['cargo', 'tree', '--locked', '--offline', '-p', package, '-e', 'normal,build', '--target', 'x86_64-unknown-linux-gnu', '--prefix', 'none', '--format', '{p}'], cwd=exported, env=env, text=True)
        keys = {(parts[0], parts[1][1:]) for line in tree.splitlines() if len(parts := line.split()) >= 2 and parts[1].startswith('v')}
        packages = [p for p in metadata['packages'] if (p['name'], p['version']) in keys and p['id'] not in members]
        if not packages:
            raise ValueError('empty scanner dependency closure')
        closure[binary] = [{'name': p['name'], 'version': p['version'], 'source': p['source'], 'license': p['license']} for p in sorted(packages, key=artifact.crate_key)]
        selected.update({p['id']: p for p in packages})
    write_json(out / 'dependency-closure.json', closure)
    lines = ['# Scanner dependency licenses', '', 'Normal and build dependencies for both scanner executables; resolved with locked offline Cargo.', '']
    for package in sorted(selected.values(), key=artifact.crate_key):
        lines += artifact.render_crate(package, artifact.license_texts(package))
    (out / 'THIRD-PARTY-LICENSES.md').write_text('\n'.join(lines) + '\n')
    shutil.copyfile(exported / 'LICENSE', out / 'LICENSE')
    evidence = ['source-inputs.json', 'dependency-closure.json', 'THIRD-PARTY-LICENSES.md', 'LICENSE', 'build.log', 'repro.log', 'test.log']
    evidence += [binary + '.ldd' for binary in BINARIES]
    write_json(out / 'provenance.json', {
        'schemaVersion': 1, 'sourceCommit': pins['sourceCommit'], 'sourceTree': pins['sourceTree'],
        'cargoLockSha256': sha(exported / 'Cargo.lock'), 'cargoTomlSha256': sha(exported / 'Cargo.toml'),
        'builderImage': pins['builderImage'], 'rustcVv': subprocess.check_output(['rustc', '-vV'], text=True),
        'rpmInventorySha256': sha(args.rpm_inventory), 'buildCommand': BUILD,
        'sourceDateEpoch': env['SOURCE_DATE_EPOCH'], 'reproducible': True, 'testsPassed': True,
        'binaries': {binary: sha(out / binary) for binary in BINARIES},
        'evidence': {name: sha(out / name) for name in evidence},
        'sourceIdentity': 'pinned upstream source, unmodified; no tower patch is applied',
    })
    print('scanner build/test/repro/dependency evidence complete', flush=True)


# Arguments each phase takes (all required); any other is refused.
PHASE_ARGS = {'inputs': ('source', 'inputs', 'cargo-home', 'pins', 'toolchain-digest'),
              'build': ('inputs', 'cargo-home', 'out', 'pins', 'rpm-inventory')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'inputs', 'cargo-home', 'out', 'pins', 'rpm-inventory', 'toolchain-digest', 'record-toolchain'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--phase', choices=tuple(PHASE_ARGS))
    args = parser.parse_args()
    if args.record_toolchain:
        # Right after the toolchain install, before any third-party build script has run.
        if not args.pins:
            parser.error('--record-toolchain needs --pins')
        args.record_toolchain.write_text(installed_toolchain_digest(json.loads(args.pins.read_text())) + '\n')
        return
    if args.phase is None:
        parser.error('one of --phase inputs|build is required')
    for name in ('source', 'inputs', 'cargo-home', 'out', 'pins', 'rpm-inventory', 'toolchain-digest'):
        given = getattr(args, name.replace('-', '_')) is not None
        if name in PHASE_ARGS[args.phase] and not given:
            parser.error('--%s is required' % name)
        if name not in PHASE_ARGS[args.phase] and given:
            parser.error('--%s is not used by the %s phase' % (name, args.phase))
    (inputs_phase if args.phase == 'inputs' else build_phase)(args)


if __name__ == '__main__':
    main()
