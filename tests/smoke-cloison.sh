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
# UNVERIFIED against a built image (no image build in the scaffold PR).
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

echo "== M1 adds no kargs =="
check "no kargs.d fragment named for cloison" \
    bash -c '! compgen -G "/usr/lib/bootc/kargs.d/*cloison*" >/dev/null'
check "no kargs.d fragment mentions cloison" \
    bash -c '! grep -rqi cloison /usr/lib/bootc/kargs.d/'
check "no kargs.d fragment binds vfio-pci.ids" \
    bash -c '! grep -rqiE "vfio[-_]pci\.ids" /usr/lib/bootc/kargs.d/'

echo "== M1 adds no VFIO binding =="
check "no vfio-pci ids binding in modprobe.d/dracut.conf.d" \
    bash -c '! grep -rqiE "vfio[-_]pci[[:space:]]+ids=|vfio[-_]pci\.ids" /etc/modprobe.d /usr/lib/modprobe.d /etc/dracut.conf.d /usr/lib/dracut/dracut.conf.d 2>/dev/null'

echo "== M1 enables no units =="
check "no cloison* unit file shipped" \
    bash -c '! find /usr/lib/systemd /etc/systemd -iname "cloison*" 2>/dev/null | grep -q .'
usbguard_state="$(systemctl is-enabled usbguard.service 2>/dev/null || true)"
case "${usbguard_state}" in
    enabled|enabled-runtime|static|alias|indirect|generated) bad "usbguard.service not enabled (got '${usbguard_state}')" ;;
    *) pass "usbguard.service not enabled (${usbguard_state:-absent})" ;;
esac

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "CLOISON SMOKE TESTS FAILED"
    exit 1
fi
echo "All cloison smoke checks passed."
exit 0
