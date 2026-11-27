#!/usr/bin/env python3
"""Read-only early warning that pinned Koji signed RPMs are still downloadable.

Collects every (name, kojiBase + kojiPath, size) from the three snitchwatch
toolchain pin files with the same validation rules as
build_files/firewall/snitchwatch-system-toolchain.sh, then HEADs each URL.
Koji purges signed copies of superseded builds; today that only surfaces as
"download failed" when the image build runs.

Exit: 0 all OK | 1 any GONE (404/410), FORBIDDEN (401/403 on every attempt) or
SIZE_MISMATCH | 3 only TRANSIENT problems (any other response or error, cause
printed; not healthy, not definitive) | 2 usage or malformed/unreadable pin.
A total deadline bounds the run; entries not reached are TRANSIENT (deadline).
No secrets are used or printed; sha256 pins are never read.
"""
import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional, Sequence
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = tuple(
    ROOT / 'build_files/firewall' / name
    for name in (
        'snitchwatch-system-pins.json',
        'snitchwatch-system-daemon-pins.json',
        'snitchwatch-system-ebpf-pins.json',
    )
)
TESTING_ENV = 'KOJI_HEAD_CHECK_TESTING'
BASE_RE = re.compile(r'https://[A-Za-z0-9._-]+(:[0-9]+)?/[A-Za-z0-9._+/-]*')
OVERRIDE_RE = re.compile(r'https?://[A-Za-z0-9._-]+(:[0-9]+)?/[A-Za-z0-9._+/-]*')
PATH_RE = re.compile(r'[A-Za-z0-9._+/-]+')
NAME_RE = re.compile(r'[A-Za-z0-9._+-]+')
ATTEMPTS = 3
MAX_REDIRECTS = 3
OK, GONE, SIZE_MISMATCH, TRANSIENT = 'OK', 'GONE', 'SIZE_MISMATCH', 'TRANSIENT'
FORBIDDEN = 'FORBIDDEN'
DEFINITIVE = (GONE, SIZE_MISMATCH, FORBIDDEN)
DEFAULT_TIMEOUT, DEFAULT_DEADLINE = 8.0, 420.0


class PinError(Exception):
    """A pin file is unreadable or malformed; the whole run is refused."""


class Entry(NamedTuple):
    name: str
    path: str
    url: str
    size: Optional[int]


class Result(NamedTuple):
    status: str
    name: str
    path: str
    cause: str = ''


def parse_pin_file(pin_file: Path, base_override: Optional[str]) -> list:
    try:
        data = json.loads(pin_file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PinError(f'{pin_file.name}: unreadable or invalid JSON ({exc.__class__.__name__})') from exc
    if not isinstance(data, dict):
        raise PinError(f'{pin_file.name}: top level must be an object')
    base = data.get('kojiBase')
    if not isinstance(base, str) or not BASE_RE.fullmatch(base) or not base.endswith('/'):
        raise PinError(f'{pin_file.name}: kojiBase must be an https URL ending in /')
    rpms = data.get('toolchainRpms')
    if not isinstance(rpms, list) or not rpms:
        raise PinError(f'{pin_file.name}: toolchainRpms must be a non-empty list')
    return [parse_entry(pin_file.name, rpm, base_override or base) for rpm in rpms]


def parse_entry(label: str, rpm: object, base: str) -> Entry:
    if not isinstance(rpm, dict):
        raise PinError(f'{label}: toolchainRpms entry must be an object')
    name, path, size = rpm.get('name'), rpm.get('kojiPath'), rpm.get('size')
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise PinError(f'{label}: malformed rpm name')
    if not isinstance(path, str) or not PATH_RE.fullmatch(path) or path.startswith('/') or '..' in path:
        raise PinError(f'{label}: unsafe kojiPath for {name}')
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise PinError(f'{label}: malformed size pin for {name}')
    return Entry(name, path, base + path, size)


def collect(pin_files: Sequence[Path], base_override: Optional[str]) -> list:
    return [entry for pin_file in pin_files for entry in parse_pin_file(pin_file, base_override)]


class StrictRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a bounded number of redirects, https only (plus the test scheme), keeping HEAD."""

    max_redirections = MAX_REDIRECTS

    def __init__(self, allowed_schemes: frozenset) -> None:
        self._allowed = allowed_schemes

    def redirect_request(self, _req, _fp, code, _msg, _headers, newurl):
        if urlsplit(newurl).scheme not in self._allowed:
            raise urllib.error.URLError(f'redirect to disallowed scheme ({code})')
        return urllib.request.Request(newurl, method='HEAD', headers={'User-Agent': 'koji-head-check'})


def classify_ok(entry: Entry, status: int, content_length: Optional[str]) -> tuple:
    if status != 200:
        return TRANSIENT, f'HTTP {status}'
    if entry.size is None or content_length is None:
        return OK, ''
    if not content_length.isdigit():
        return TRANSIENT, 'bad Content-Length'
    return (OK, '') if int(content_length) == entry.size else (SIZE_MISMATCH, f'Content-Length {content_length}')


def head_once(entry: Entry, timeout: float, allowed_schemes: frozenset) -> tuple:
    """Return (status, cause) for one HEAD; cause is empty for OK."""
    opener = urllib.request.build_opener(StrictRedirect(allowed_schemes))
    request = urllib.request.Request(entry.url, method='HEAD', headers={'User-Agent': 'koji-head-check'})
    try:
        with opener.open(request, timeout=timeout) as resp:
            return classify_ok(entry, resp.status, resp.headers.get('Content-Length'))
    except urllib.error.HTTPError as exc:
        return (GONE, f'HTTP {exc.code}') if exc.code in (404, 410) else (TRANSIENT, f'HTTP {exc.code}')
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        return TRANSIENT, exc.__class__.__name__


def check(entry: Entry, timeout: float, backoff: float, allowed_schemes: frozenset, deadline: float) -> Result:
    """HEAD with retries; `deadline` is an absolute time.monotonic() value."""
    outcomes = []
    for attempt in range(ATTEMPTS):
        if time.monotonic() >= deadline:
            return Result(TRANSIENT, entry.name, entry.path, 'deadline')
        status, cause = head_once(entry, timeout, allowed_schemes)
        outcomes.append((status, cause))
        if status != TRANSIENT:
            return Result(status, entry.name, entry.path, cause)
        if attempt < ATTEMPTS - 1:
            time.sleep(backoff * (attempt + 1))
    causes = {c for _, c in outcomes}
    if len(causes) == 1 and causes <= {'HTTP 401', 'HTTP 403'}:
        return Result(FORBIDDEN, entry.name, entry.path, causes.pop())
    return Result(TRANSIENT, entry.name, entry.path, outcomes[-1][1])


def format_result(r: Result) -> str:
    detail = f' ({r.cause})' if r.cause and r.status in (TRANSIENT, FORBIDDEN) else ''
    return f'{r.status} {r.name} {r.path}{detail}'


def exit_code(results: Sequence[Result]) -> int:
    statuses = {r.status for r in results}
    if statuses & set(DEFINITIVE):
        return 1
    return 3 if TRANSIENT in statuses else 0


def summary_line(results: Sequence[Result]) -> str:
    counts = ' '.join(f'{s}={sum(r.status == s for r in results)}' for s in (OK, GONE, SIZE_MISMATCH, FORBIDDEN, TRANSIENT))
    return f'koji-head-check: {len(results)} checked {counts}'


def write_step_summary(results: Sequence[Result], summary: str) -> None:
    target = os.environ.get('GITHUB_STEP_SUMMARY')
    if not target:
        return
    rows = ''.join(f'| {r.status} | `{r.name}` | `{r.path}` | {r.cause} |\n' for r in results)
    body = f'### Koji signed-RPM availability\n\n| Status | Name | Path | Cause |\n|---|---|---|---|\n{rows}\n{summary}\n'
    try:
        with open(target, 'a', encoding='utf-8') as fh:
            fh.write(body)
    except OSError as exc:
        print(f'koji-head-check: cannot write step summary: {exc}', file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    p.add_argument('--list', action='store_true', help='print what would be checked; no network')
    p.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='per-request timeout in seconds')
    p.add_argument('--deadline', type=float, default=DEFAULT_DEADLINE, help='total seconds before remaining entries are TRANSIENT (deadline)')
    p.add_argument('--backoff', type=float, default=2.0, help='base retry backoff in seconds')
    p.add_argument('--pins', action='append', type=Path, help=f'test-only: pin file (repeatable); needs {TESTING_ENV}=1')
    p.add_argument('--base-url-override', help=f'test-only: replaces kojiBase; needs {TESTING_ENV}=1')
    return p


def run(args: argparse.Namespace) -> int:
    pins = tuple(args.pins) if args.pins else DEFAULT_PINS
    override = args.base_url_override
    if override is not None and not (OVERRIDE_RE.fullmatch(override) and override.endswith('/')):
        raise PinError('--base-url-override must be an http(s) URL ending in /')
    entries = collect(pins, override)
    if args.list:
        for e in entries:
            print(f'WOULD-CHECK {e.name} {e.path}' + ('' if e.size is None else f' size={e.size}'))
        print(f'koji-head-check: {len(entries)} entries would be checked')
        return 0
    schemes = frozenset({'https', urlsplit(override).scheme}) if override else frozenset({'https'})
    deadline = time.monotonic() + args.deadline
    results = [check(e, args.timeout, args.backoff, schemes, deadline) for e in entries]
    for r in results:
        print(format_result(r))
    summary = summary_line(results)
    print(summary)
    write_step_summary(results, summary)
    return exit_code(results)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.pins or args.base_url_override) and os.environ.get(TESTING_ENV) != '1':
        parser.error(f'--pins/--base-url-override are test-only and refused unless {TESTING_ENV}=1')
    try:
        return run(args)
    except PinError as exc:
        print(f'koji-head-check: refusing to run: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
