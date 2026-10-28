#!/usr/bin/env python3
"""Build and test the scanner from the pinned upstream tree in a disposable native builder.

Never invoke the installed privileged entrypoint, pkexec, or a live scan.
The caller supplies the already verified clean source and pinned toolchain.
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

BINARIES = ('snitchwatch-scanner-privileged', 'snitchwatch-scanner-broker')
PACKAGES = ('scanner-privileged', 'scanner-broker')
BUILD = ['cargo', 'build', '--release', '--locked', '--offline', '-p', PACKAGES[0], '-p', PACKAGES[1]]
TOOLCHAIN_OVERRIDES = (
    'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'CARGO_BUILD_RUSTC_WRAPPER',
    'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER', 'RUSTC', 'RUSTDOC', 'CARGO_BUILD_RUSTC',
    'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER',
    'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS', 'CARGO_BUILD_RUSTFLAGS',
    'CARGO_ENCODED_RUSTFLAGS', 'RUSTFLAGS',
)


def clean_environment(environment):
    # Children must never write bytecode caches into the pinned checkouts.
    return {**{key: value for key, value in environment.items() if key not in TOOLCHAIN_OVERRIDES}, 'PYTHONDONTWRITEBYTECODE': '1'}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'out', 'pins', 'rpm-inventory'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    if not (Path('/run/.containerenv').exists() or Path('/.dockerenv').exists()) or Path('/run/host').exists():
        raise ValueError('scanner build requires the fresh native builder')
    source, out = args.source.resolve(), args.out.resolve()
    if out.exists() or out == Path('/'):
        raise ValueError('output must be a new isolated directory')
    pins = json.loads(args.pins.read_text())
    out.mkdir()
    exported = out / 'source'
    exported.mkdir()
    archive = out / 'source.tar'
    with archive.open('wb') as output:
        subprocess.run(['git', '-C', str(source), 'archive', pins['sourceCommit']], stdout=output, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(exported, filter='data')
    archive.unlink()
    require_pinned_scanner_sources(exported, pins)
    write_json(out / 'source-inputs.json', {str(path.relative_to(exported)): sha(path)
               for path in sorted(exported.rglob('*')) if path.is_file()})
    # Each build gets an empty target and identical remapped source/cargo/target
    # paths. Network is disabled after the bridge factory's locked cargo fetch.
    env = clean_environment(os.environ)
    env['SOURCE_DATE_EPOCH'] = subprocess.check_output(['git', '-C', str(source), 'log', '-1', '--format=%ct'], text=True).strip()
    env['CARGO_INCREMENTAL'] = '0'
    env['CARGO_NET_OFFLINE'] = 'true'
    targets = [out / 'target-a', out / 'target-b']
    for index, target in enumerate(targets):
        env['CARGO_TARGET_DIR'] = str(target)
        env['RUSTFLAGS'] = f'--remap-path-prefix={exported}=/snitchwatch --remap-path-prefix={env["CARGO_HOME"]}=/cargo --remap-path-prefix={target}=/target'
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
    env['RUSTFLAGS'] = f'--remap-path-prefix={exported}=/snitchwatch --remap-path-prefix={env["CARGO_HOME"]}=/cargo --remap-path-prefix={targets[0]}=/target'
    run(['cargo', 'test', '--locked', '--offline', '-p', PACKAGES[0], '-p', PACKAGES[1], '--', '--test-threads=1'], exported, env, out / 'test.log')
    bus_log = out / 'private-bus.log'
    run(['dbus-run-session', '--', 'cargo', 'test', '--locked', '--offline', '-p', PACKAGES[1], 'private_bus', '--', '--ignored', '--test-threads=1'], exported, env, bus_log)
    require_private_bus_test(bus_log.read_text(errors='replace'))
    with (out / 'test.log').open('ab') as combined:
        combined.write(bus_log.read_bytes())
    bus_log.unlink()
    # Resolve each executable's own normal+build graph, rather than borrowing
    # the bridge's license closure. Include the actual license/notice texts.
    artifact = load_bridge_artifact(source)
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
    shutil.copyfile(source / 'LICENSE', out / 'LICENSE')
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


if __name__ == '__main__':
    main()
