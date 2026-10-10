#!/usr/bin/env bash
# Install the reviewed Go/protoc/libnetfilter_queue toolchain from pinned,
# Fedora-signed Koji RPMs instead of whatever the live repos currently serve.
# Usage: snitchwatch-system-toolchain.sh install <pins.json> <workdir> [extra-live-dnf-pkgs...]
#        snitchwatch-system-toolchain.sh fetch   <pins.json> <workdir>
# Each RPM must match its pinned size and sha256, carry a valid Fedora
# signature (plain "digests OK" means unsigned and is refused) and report the
# pinned NEVRA before one dnf transaction installs them with the extra packages.
# "fetch" does the same verification but leaves the RPMs in <workdir>/rpms and
# installs nothing (for a later offline install).
set -euo pipefail

die() { printf 'snitchwatch-toolchain: %s\n' "$*" >&2; exit 1; }

[[ "${1:-}" == install || "${1:-}" == fetch ]] && [[ $# -ge 3 ]] ||
    die 'usage: install <pins.json> <workdir> [extra-dnf-pkgs...] | fetch <pins.json> <workdir>'
mode=$1
[[ "$mode" == install || $# -eq 3 ]] || die 'fetch takes no extra packages'
pins=$2
work=$3
shift 3
extras=("$@")
[[ -r "$pins" ]] || die "cannot read pins file: $pins"
# The pins are parsed with python3. snitchwatch-system-build.sh runs install-deps.sh
# (which installs python3) earlier in the same RUN, so it is present in the build;
# fail with a clear message if this helper is ever run on its own without it.
command -v python3 > /dev/null || die 'python3 is required to read the pins (install it first)'

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

python3 - "$pins" > "$tmp/pins.tsv" <<'PY' || die 'cannot parse toolchain pins'
import json,sys
p=json.load(open(sys.argv[1]))
k=p['rpmSigningKey']
print('\t'.join((p['kojiBase'],k['path'],k['sha256'],k['fingerprint'])))
for r in p['toolchainRpms']:
    print('\t'.join((r['name'],r['file'],r['kojiPath'],r['sha256'],str(r['size']),r['nevra'])))
PY
IFS=$'\t' read -r koji_base key_path key_sha key_fpr < "$tmp/pins.tsv"
[[ "$key_sha" =~ ^[0-9a-f]{64}$ ]] || die 'malformed signing-key sha256 pin'
[[ "$key_fpr" =~ ^[0-9A-Fa-f]{40}$ ]] || die 'malformed signing-key fingerprint pin'

[[ "$koji_base" =~ ^https://[A-Za-z0-9._-]+(:[0-9]+)?/[A-Za-z0-9._+/-]*$ && "$koji_base" == */ ]] ||
    die "kojiBase must be an https URL ending in /: $koji_base"
[[ "$(sha256sum -- "$key_path" 2>/dev/null | cut -d' ' -f1)" == "$key_sha" ]] ||
    die "Fedora RPM signing key does not match the pinned sha256: $key_path"

mkdir -p -- "$work/rpms"
rpmdb="$tmp/rpmdb"
mkdir -p -- "$rpmdb"
rpm --dbpath "$rpmdb" --import "$key_path" || die 'cannot import the Fedora signing key into the throwaway rpm db'
# rpm 6 reports the full fingerprint as the gpg-pubkey version; older rpm reports the
# 8-hex key id, so accept the pinned fingerprint ending with what was imported.
imported=$(rpm --dbpath "$rpmdb" -qa gpg-pubkey --qf '%{VERSION}\n')
imported=${imported,,}
fpr_lc=${key_fpr,,}
[[ "$imported" =~ ^[0-9a-f]{8,40}$ && "$fpr_lc" == *"$imported" ]] ||
    die "imported signing key does not match the pinned fingerprint ($key_fpr): $imported"

files=()
nevras=()
names=()
hdrs=()
while IFS=$'\t' read -r name file path want_sha want_size nevra; do
    [[ "$path" =~ ^[A-Za-z0-9._+/-]+$ && "$path" != /* && "$path" != *..* ]] || die "unsafe kojiPath for $name: $path"
    [[ "$file" =~ ^[A-Za-z0-9._+-]+$ && "$file" != *..* ]] || die "unsafe file name for $name: $file"
    [[ "$want_sha" =~ ^[0-9a-f]{64}$ && "$want_size" =~ ^[0-9]+$ ]] || die "malformed sha256/size pin for $name"
    dest="$work/rpms/$file"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 --max-time 300 --retry 2 \
        --output "$dest" "$koji_base$path" || die "download failed for $name"
    [[ -s "$dest" ]] || die "empty download for $name"
    [[ "$(stat -c %s -- "$dest")" == "$want_size" ]] || die "size mismatch for $name"
    [[ "$(sha256sum -- "$dest" | cut -d' ' -f1)" == "$want_sha" ]] || die "sha256 mismatch for $name"
    sigout=$(rpm --dbpath "$rpmdb" -K "$dest") || die "rpm -K failed for $name: $sigout"
    [[ "$sigout" == *'digests signatures OK'* ]] || die "$name is unsigned or not signed by the pinned Fedora key: $sigout"
    [[ "$(rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}' "$dest")" == "$nevra" ]] ||
        die "NEVRA mismatch for $name (expected $nevra)"
    files+=("$dest")
    nevras+=("$nevra")
    names+=("$name")
    hdrs+=("$(rpm -qp --qf '%{SHA256HEADER}' "$dest")")
done < <(tail -n +2 "$tmp/pins.tsv")
[[ ${#files[@]} -gt 0 ]] || die 'no toolchainRpms pinned'

[[ "$mode" == install ]] || exit 0

timeout 240 dnf -y --setopt=install_weak_deps=False install "${files[@]}" "${extras[@]}" || die 'dnf install failed'

for i in "${!names[@]}"; do
    [[ "$(rpm -q --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}' "${names[$i]}")" == "${nevras[$i]}" ]] ||
        die "installed ${names[$i]} is not the pinned ${nevras[$i]}"
    # Same NEVRA is not enough: the installed header must be the one we verified,
    # not a same-version copy that dnf took from a repo instead of the checked file.
    [[ -n "${hdrs[$i]}" && "$(rpm -q --qf '%{SHA256HEADER}' "${names[$i]}")" == "${hdrs[$i]}" ]] ||
        die "installed ${names[$i]} is not the verified file (header digest differs)"
done
