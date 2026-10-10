#!/usr/bin/env python3
"""Offline fixture gate; evidence is retained, runtime is never certified."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    ('toolchain', ('bash', 'tests/test-snitchwatch-toolchain.sh'), ('bash', 'python3', 'sha256sum', 'mktemp', 'cp', 'cat', 'chmod', 'mkdir', 'rm', 'basename', 'dirname', 'sed', 'grep', 'cut', 'stat', 'tail', 'timeout', 'env')),
    ('image-build', ('python3', 'tests/test-snitchwatch-image-build.py'), ('bash', 'python3', 'cat', 'mkdir', 'touch')),
    ('daemon-image-build', ('python3', 'tests/test-snitchwatch-image-build-daemon.py'), ('python3', 'git')),
    ('system-fixtures', ('bash', 'tests/test-snitchwatch-system.sh'), ('bash', 'python3')),
    ('supply-chain', ('bash', 'tests/check-supply-chain.sh'), ('bash', 'python3', 'jq')),
)
INPUTS = (
    'Justfile', 'Containerfile', 'cosign.pub', '.github/workflows/build.yml',
    'build_files/firewall', 'build_files/build.d/15-signature-policy.sh',
    'build_files/build.d/95-firewall.sh', 'system_files',
    'scripts/install-signature-policy.sh', 'scripts/snitchwatch-gate.py',
    'tests/test-snitchwatch-image-build-fixtures',
    'tests/test-snitchwatch-system.py', 'tests/test-snitchwatch-gate.py',
    *(command[1] for _, command, _ in CHECKS),
)
ADAPTATIONS = {
    'tests/test-snitchwatch-toolchain.sh': (('T', 1), ('T/work', 2)),
    'build_files/firewall/snitchwatch-system-toolchain.sh': (('tmp', 1),),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def cleanup(path):
    root = Path(os.environ['SNITCHWATCH_GATE_SCRATCH']).resolve()
    target = Path(path)
    if target.is_symlink() or root not in target.resolve().parents:
        raise ValueError('cleanup outside gate scratch refused')
    if target.exists():
        shutil.rmtree(target)


def snapshot(root, destination):
    records = {}
    for name in INPUTS:
        source = root / name
        paths = sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in paths:
            if path.is_symlink():
                raise ValueError(f'symlink input refused: {path.relative_to(root)}')
            if path.is_dir():
                continue
            relative = path.relative_to(root)
            if path.name == 'cosign.key':
                raise ValueError('private key input refused')
            data = path.read_bytes()
            mode = path.stat().st_mode & 0o777
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(mode)
            records[str(relative)] = {'sha256': digest(data), 'mode': mode}
    adapted = {}
    for name, replacements in ADAPTATIONS.items():
        path = destination / name
        text = path.read_text()
        for variable, count in replacements:
            expression = '$T/work' if variable == 'T/work' else '$' + variable
            old = f'rm -rf -- "{expression}"'
            if text.count(old) != count:
                raise ValueError(f'cleanup contract changed: {name}')
            text = text.replace(old, f'python3 "$SNITCHWATCH_GATE_CLEANUP" --cleanup "{expression}"')
        if 'rm -rf' in text:
            raise ValueError(f'unadapted recursive cleanup: {name}')
        path.write_text(text)
        adapted[name] = digest(path.read_bytes())
    return records, adapted


def execute(command, cwd, env, logfile, timeout=120):
    with logfile.open('wb') as log:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as error:
            log.write(str(error).encode())
            return 'BLOCKED', None
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            log.write(b'\nBLOCKED: check timed out\n')
            return 'BLOCKED', None
    return ('PASS' if code == 0 else 'FAIL'), code


def first_error(logfile):
    lines = logfile.read_text(errors='replace').splitlines()
    for line in lines:
        if any(word in line.lower() for word in ('error', 'fail', 'blocked', 'traceback', 'assert')):
            return line[:240]
    return next((line[:240] for line in lines if line.strip()), 'no diagnostic output')


def run(root, evidence):
    snapshot_root = evidence / 'inputs'
    scratch = evidence / 'scratch'
    scratch.mkdir()
    results = []
    summary = {'schema': 1, 'scope': 'offline-fixtures', 'runtime': 'UNVERIFIED', 'checks': results}
    env = {key: value for key, value in os.environ.items() if key in ('PATH', 'LANG')}
    env.update(HOME=str(scratch), TMPDIR=str(scratch), LC_ALL='C',
               PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL='/dev/null', SNITCHWATCH_GATE_SCRATCH=str(scratch),
               SNITCHWATCH_GATE_CLEANUP=str(snapshot_root / 'scripts/snitchwatch-gate.py'))
    blocked = None
    try:
        records, adapted = snapshot(root, snapshot_root)
        manifest = {'inputs': records, 'adapted_sha256': adapted}
        save(evidence / 'inputs.json', manifest)
        summary['inputs_sha256'] = digest((evidence / 'inputs.json').read_bytes())
    except (OSError, ValueError) as error:
        blocked = str(error)
    isolation = evidence / 'isolation.log'
    if not blocked:
        state, _ = execute(['unshare', '--user', '--map-root-user', '--net', 'true'],
                           snapshot_root, env, isolation, timeout=10)
        if state != 'PASS':
            blocked = 'network isolation unavailable: ' + first_error(isolation)
    for name, command, dependencies in CHECKS:
        logfile = evidence / (name + '.log')
        missing = [tool for tool in dependencies if not shutil.which(tool, path=env.get('PATH'))]
        reason = blocked or (('missing tools: ' + ', '.join(missing)) if missing else None)
        if reason:
            logfile.write_text('BLOCKED: ' + reason + '\n')
            status, code = 'BLOCKED', None
        else:
            status, code = execute(['unshare', '--user', '--map-root-user', '--net', *command],
                                   snapshot_root, env, logfile)
        result = {'name': name, 'status': status, 'exit_code': code,
                  'command': list(command), 'log': logfile.name,
                  'log_sha256': digest(logfile.read_bytes())}
        if status != 'PASS':
            result['first_error'] = first_error(logfile)
        results.append(result)
        print(f"{name}: {status}" + (f" — {result['first_error']}" if status != 'PASS' else ''))
    summary['offline'] = ('FAIL' if any(r['status'] == 'FAIL' for r in results)
                          else 'BLOCKED' if any(r['status'] == 'BLOCKED' for r in results) else 'PASS')
    save(evidence / 'summary.json', summary)
    print(f"offline: {summary['offline']}; runtime: UNVERIFIED\nevidence: {evidence}")
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[summary['offline']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, help='new directory; default: unique /tmp directory')
    parser.add_argument('--cleanup', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cleanup is not None:
        cleanup(args.cleanup)
        return 0
    if args.evidence:
        evidence = args.evidence.resolve()
        for name in INPUTS:
            source = ROOT / name
            if evidence == source or source in evidence.parents:
                parser.error('evidence must be outside input paths')
        evidence.mkdir(parents=True, exist_ok=False)
    else:
        evidence = Path(tempfile.mkdtemp(prefix='snitchwatch-gate.'))
    return run(ROOT, evidence)


if __name__ == '__main__':
    sys.exit(main())
