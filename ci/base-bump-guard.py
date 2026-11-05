#!/usr/bin/env python3
"""Decide whether a base-bump PR may auto-merge. Fails closed.

Prints `automerge=true|false` and `reason=<one line>`; always exits 0 so the
caller can read the decision. true only when the Fedora major is unchanged,
the kernel is the same or newer, and the new base meets the refresh policy
floor (minimumBaseVersion / minimumKernel) and the blast-radius package diff
between the current and new base is empty (--blast-report, produced by
ci/base-blast-diff.sh; a missing/unreadable report is a `false`). Parsing is reused from
ci/base-refresh.py; any parse or policy error is a `false`.
"""
import argparse
import importlib.util
import re
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def load_refresh():
    spec = importlib.util.spec_from_file_location('base_refresh', HERE / 'base-refresh.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


NO_CHANGES = '_No blast-radius package changes._'
NAME_LINE = re.compile(r'^- `([^`]*)`', re.M)
MAX_NAMES, MAX_NAME_LEN = 5, 40


def blast_reason(report_path):
    """Return None if the blast-radius diff is provably empty, else a one-line reason."""
    if not report_path:
        return 'blast-radius package diff not computed'
    try:
        text = Path(report_path).read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return 'blast-radius package diff not computed'
    if not text:
        return 'blast-radius package diff not computed'
    if text == NO_CHANGES:
        return None
    names = [re.sub(r'[^0-9A-Za-z._+-]', '', n)[:MAX_NAME_LEN] for n in NAME_LINE.findall(text)]
    shown = ', '.join(n for n in names[:MAX_NAMES] if n)
    more = len(names) - MAX_NAMES
    return 'blast-radius packages changed (%d): %s%s; human review required' % (
        len(names), shown or 'unparsed', ' and %d more' % more if more > 0 else '')


def decide(refresh, root, cur_version, cur_kernel, new_version, new_kernel, blast_report=''):
    """Return (automerge, reason). Raises on malformed input (caller fails closed)."""
    cur_v, new_v = refresh.base_version(cur_version), refresh.base_version(new_version)
    cur_k, new_k = refresh.kernel_version(cur_kernel), refresh.kernel_version(new_kernel)
    # kernel tuple: (major, minor, patch, ogc, ogc-rel, fedora)
    if cur_k[5] != new_k[5] or cur_v[0] != new_v[0]:
        return False, 'Fedora major changed (%s -> %s); human review required' % (cur_kernel, new_kernel)
    if new_k < cur_k:
        return False, 'kernel downgrade (%s -> %s)' % (cur_kernel, new_kernel)
    if new_v < cur_v:
        return False, 'base version downgrade (%s -> %s)' % (cur_version, new_version)
    policy = refresh.policy(root)
    if new_v < refresh.base_version(policy['minimumBaseVersion']):
        return False, 'base %s below policy minimumBaseVersion %s' % (new_version, policy['minimumBaseVersion'])
    if new_k < refresh.kernel_version(policy['minimumKernel']):
        return False, 'kernel %s below policy minimumKernel %s' % (new_kernel, policy['minimumKernel'])
    blast = blast_reason(blast_report)
    if blast:
        return False, blast
    return True, 'same Fedora major, kernel %s -> %s (same or newer), policy floor met, no blast-radius package changes' % (
        cur_kernel, new_kernel)


def main(argv):
    parser = argparse.ArgumentParser()
    for name in ('current-version', 'current-kernel', 'new-version', 'new-kernel'):
        parser.add_argument('--' + name, default='')
    parser.add_argument('--blast-report', default='')
    parser.add_argument('--root', default=str(HERE.parent))
    args = parser.parse_args(argv)
    try:
        ok, reason = decide(load_refresh(), Path(args.root), args.current_version, args.current_kernel,
                            args.new_version, args.new_kernel, args.blast_report)
    except (Exception, SystemExit) as error:
        ok, reason = False, 'guard failed closed: %s' % error
    reason = ' '.join(reason.split())
    print('automerge=%s' % ('true' if ok else 'false'))
    print('reason=%s' % reason)


if __name__ == '__main__':
    main(sys.argv[1:])
