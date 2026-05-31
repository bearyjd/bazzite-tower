#!/usr/bin/env python3
"""Diff the upstream base image's package manifest against the last-seen one,
filtered to the packages whose changes can plausibly break bazzite-tower's
downstream customizations (the QEMU/libvirt stack, the Wi-Fi backend guard,
Docker CE, and the boot/kargs path).

Emits a markdown report to stdout and, when running under GitHub Actions, writes
`changed` (true/false) and a multiline `report` to $GITHUB_OUTPUT.

Usage:
    base-diff.py <old-manifest> <new-manifest>

Manifest line format (one package per line, sorted):
    NAME VERSION-RELEASE.ARCH
"""
import os
import re
import sys

# Matched against the package NAME. These are the packages whose version bump or
# removal has a realistic path to breaking one of our customizations:
#   qemu*/libvirt*/edk2-ovmf/swtpm/virt-*  -> the virtualization stack + the
#                                              sysusers/user + modular-daemon logic
#   NetworkManager*/iwd/wpa_supplicant     -> the Wi-Fi backend guard's assumptions
#   polkit*                                -> the wheel -> qemu:///system rule API
#   systemd*                               -> unit semantics / sysusers behaviour
#   kernel*                                -> IOMMU / passthrough
#   bootc                                  -> kargs.d handling
#   docker-ce*/containerd*/moby*           -> the Docker CE layer + $releasever
BLAST_RADIUS = re.compile(
    r"^(qemu.*|libvirt.*|edk2-ovmf|swtpm.*|virt-(install|manager|viewer)|"
    r"NetworkManager.*|iwd|wpa_supplicant|"
    r"polkit.*|systemd.*|kernel.*|bootc|"
    r"docker-ce.*|containerd.*|moby.*)$"
)


def load(path):
    """Return {name: evr} for a manifest file, or None if it doesn't exist."""
    if not path or not os.path.exists(path):
        return None
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            name, _, evr = line.partition(" ")
            out[name] = evr
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: base-diff.py <old-manifest> <new-manifest>")

    old = load(sys.argv[1])
    new = load(sys.argv[2])
    if new is None:
        sys.exit(f"new manifest not found: {sys.argv[2]}")

    first_run = old is None
    old = old or {}

    changed, added, removed = [], [], []
    for name in sorted(set(old) | set(new)):
        if not BLAST_RADIUS.match(name):
            continue
        o, n = old.get(name), new.get(name)
        if o and n and o != n:
            changed.append((name, o, n))
        elif n and not o:
            added.append((name, n))
        elif o and not n:
            removed.append((name, o))

    has_changes = bool(changed or added or removed) and not first_run

    sections = []
    if changed:
        sections.append("**Changed**\n" + "\n".join(
            f"- `{n}` {o} → {x}" for n, o, x in changed))
    if added:
        sections.append("**Added**\n" + "\n".join(
            f"- `{n}` {x}" for n, x in added))
    if removed:
        sections.append("**Removed**\n" + "\n".join(
            f"- `{n}` (was {o})" for n, o in removed))
    report = "\n\n".join(sections) if sections else \
        "_No blast-radius package changes._"

    print("First run — recording baseline.\n" if first_run else "", end="")
    print(report)

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as fh:
            fh.write(f"changed={'true' if has_changes else 'false'}\n")
            fh.write("report<<__BASE_DIFF_EOF__\n")
            fh.write(report + "\n")
            fh.write("__BASE_DIFF_EOF__\n")


if __name__ == "__main__":
    main()
