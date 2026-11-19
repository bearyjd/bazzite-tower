#!/usr/bin/env bash
# tests/smoke-cloison.sh -- offline assertions for the Cloison variant image,
# run against the built image with no VM (same shape as tests/smoke.sh):
#
#   podman run --rm -i <cloison-image> bash -s < tests/smoke-cloison.sh
#
# MILESTONE-SPECIFIC: these assertions describe M1 (scaffold) ONLY. The image
# deliberately adds no packages, daemon, enabled units, kargs, VFIO binding or
# USBGuard. Later milestones legitimately change that, and MUST change the
# matching assertions here explicitly, with reviewed intent in the same PR --
# never silently weaken or delete one (docs/prp/cloison-kvm-compartments.md,
# critic finding 12).
#
# Scope note: the variant inherits the daily image, whose kargs.d already has
# iommu/vfio-related fragments (00-iommu, 30-vfio-kvm). So "no new kargs" is
# asserted as: nothing Cloison-named or Cloison-mentioning, and no
# vfio-pci.ids binding anywhere. The daily assertions themselves are
# tests/smoke.sh, which the variant must also pass.
#
# VERIFIED 2026-11-16 against a real image built on an older local daily image
# (not the current published one); re-verify after the parent changes.
set -uo pipefail

fail=0
pass() { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fail=1; }

# check "<description>" <command...>  — passes if the command exits 0.
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then pass "${desc}"; else bad "${desc}"; fi
}

echo "== Cloison M1 scaffold markers =="
check "variant marker is exactly 'cloison-m1-scaffold'" \
    grep -qx 'cloison-m1-scaffold' /usr/share/cloison/variant
check "parent-digest file present and a sha256 digest" \
    bash -c 'grep -Eqx "sha256:[0-9a-f]{64}" /usr/share/cloison/parent-digest'
check "README present" test -f /usr/share/cloison/README

echo "== daily-derived base is present (guards the negative checks below) =="
check "kargs.d/00-iommu.toml present (inherited from daily)" test -f /usr/lib/bootc/kargs.d/00-iommu.toml
check "kargs.d/30-vfio-kvm.toml present (inherited from daily)" test -f /usr/lib/bootc/kargs.d/30-vfio-kvm.toml

echo "== M1 adds no kargs =="
check "no kargs.d fragment named for cloison" \
    bash -c 'test -d /usr/lib/bootc/kargs.d && ! compgen -G "/usr/lib/bootc/kargs.d/*cloison*" >/dev/null'
check "no kargs.d fragment mentions cloison" \
    bash -c 'test -d /usr/lib/bootc/kargs.d && ! grep -rqi cloison /usr/lib/bootc/kargs.d/'
check "no kargs.d fragment binds vfio-pci.ids" \
    bash -c 'test -d /usr/lib/bootc/kargs.d && ! grep -rqiE "vfio[-_]pci\.ids" /usr/lib/bootc/kargs.d/'

echo "== M1 adds no VFIO binding =="
# shellcheck disable=SC2016  # $d is expanded by the inner bash -c, on purpose
check "no vfio-pci ids binding in modprobe.d/dracut.conf.d" \
    bash -c 'for d in /etc/modprobe.d /usr/lib/modprobe.d /usr/lib/dracut/dracut.conf.d; do test -d "$d" || exit 2; done; ! grep -rqiE "vfio[-_]pci[[:space:]]+ids=|vfio[-_]pci\.ids" /etc/modprobe.d /usr/lib/modprobe.d /etc/dracut.conf.d /usr/lib/dracut/dracut.conf.d 2>/dev/null'

echo "== M1 enables no units =="
check "no cloison* unit file shipped" \
    bash -c 'test -d /usr/lib/systemd && test -d /etc/systemd && ! find /usr/lib/systemd /etc/systemd -iname "cloison*" | grep -q .'
check "no cloison* unit linked into any .wants/.requires dir" \
    bash -c 'test -d /etc/systemd/system && ! find /etc/systemd/system /usr/lib/systemd/system -path "*.wants/*" -iname "cloison*" -o -path "*.requires/*" -iname "cloison*" | grep -q .'
check "no preset enables a cloison* unit" \
    bash -c 'test -d /usr/lib/systemd/system-preset && ! grep -rqiE "^[[:space:]]*enable[[:space:]]+cloison" /usr/lib/systemd/system-preset /etc/systemd/system-preset 2>/dev/null'
if command -v systemctl >/dev/null 2>&1; then
    usbguard_state="$(systemctl is-enabled usbguard.service 2>/dev/null || true)"
    case "${usbguard_state}" in
        disabled|masked|not-found) pass "usbguard.service not enabled (${usbguard_state})" ;;
        *) bad "usbguard.service not enabled (got '${usbguard_state:-<empty>}')" ;;
    esac
else
    bad "systemctl missing: cannot assert usbguard.service state"
fi

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "CLOISON SMOKE TESTS FAILED"
    exit 1
fi
echo "All cloison smoke checks passed."
exit 0
