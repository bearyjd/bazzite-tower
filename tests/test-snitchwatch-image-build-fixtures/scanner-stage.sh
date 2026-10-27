#!/usr/bin/env bash
# Stage an image/package tree; never modify the live host or run input binaries.
set -euo pipefail
die() { printf '%s\n' "scanner stage: $*" >&2; exit 1; }
[[ $# -eq 5 ]] || die "usage: $0 DEST SCANNER SCANNER_SHA256 BROKER BROKER_SHA256"
dest=$(realpath -m -- "$1") || die "cannot resolve DEST"
[[ "$dest" != / ]] || die "DEST must be a staging root, not /"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

verify() {
    [[ -f "$1" && -x "$1" && ! -L "$1" ]] || die "input must be a regular executable, not a symlink"
    [[ "$2" =~ ^[0-9a-fA-F]{64}$ ]] || die "invalid SHA256"
    local actual
    actual=$(sha256sum -- "$1")
    [[ "${actual:0:64}" == "${2,,}" ]] || die "binary SHA256 mismatch"
}
# Snapshot both mutable caller paths before hashing. Only these private copies
# may be installed, so a caller changing an input after its hash cannot replace
# verified bytes. mktemp creates a directory accessible only by this user.
snapshot_dir=$(mktemp -d -- "${TMPDIR:-/tmp}/snitchwatch-scanner-stage.XXXXXXXX")
trap 'rm -f -- "$snapshot_dir/scanner" "$snapshot_dir/broker"; rmdir -- "$snapshot_dir"' EXIT
for input in "$2" "$4"; do
    [[ -f "$input" && -x "$input" && ! -L "$input" ]] || die "input must be a regular executable, not a symlink"
done
cp --no-dereference -- "$2" "$snapshot_dir/scanner"
cp --no-dereference -- "$4" "$snapshot_dir/broker"
verify "$snapshot_dir/scanner" "$3"
verify "$snapshot_dir/broker" "$5"
# Do not follow a caller's symlinks out of the staging tree, including during
# idempotent restaging. Install destinations are fixed and never caller paths.
for relative in usr usr/libexec usr/share usr/share/dbus-1 usr/share/dbus-1/services usr/share/polkit-1 usr/share/polkit-1/actions usr/lib usr/lib/tmpfiles.d; do
    [[ ! -L "$dest/$relative" ]] || die "symlink in staging destination: $relative"
done
for relative in usr/libexec/snitchwatch-scanner-privileged usr/libexec/snitchwatch-scanner-broker usr/share/dbus-1/services/org.snitchwatch.Scanner.service usr/share/polkit-1/actions/org.snitchwatch.scanner.policy usr/lib/tmpfiles.d/snitchwatch-scanner.conf; do
    [[ ! -L "$dest/$relative" ]] || die "symlink in staging destination: $relative"
done
install -Dm755 -- "$snapshot_dir/scanner" "$dest/usr/libexec/snitchwatch-scanner-privileged"
install -Dm755 -- "$snapshot_dir/broker" "$dest/usr/libexec/snitchwatch-scanner-broker"
install -Dm644 -- "$script_dir/org.snitchwatch.Scanner.service" "$dest/usr/share/dbus-1/services/org.snitchwatch.Scanner.service"
install -Dm644 -- "$script_dir/../polkit/org.snitchwatch.scanner.policy" "$dest/usr/share/polkit-1/actions/org.snitchwatch.scanner.policy"
install -Dm644 -- "$script_dir/snitchwatch-scanner.conf" "$dest/usr/lib/tmpfiles.d/snitchwatch-scanner.conf"
# Package/image installation must assign root ownership; staging works rootless.
if [[ $EUID -eq 0 ]]; then
    chown root:root "$dest/usr/libexec/snitchwatch-scanner-privileged" "$dest/usr/libexec/snitchwatch-scanner-broker" "$dest/usr/share/dbus-1/services/org.snitchwatch.Scanner.service" "$dest/usr/share/polkit-1/actions/org.snitchwatch.scanner.policy" "$dest/usr/lib/tmpfiles.d/snitchwatch-scanner.conf"
fi
