#!/usr/bin/env bash
# Verify the helper's actual group request, not only its source text.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
helper="${repo_root}/system_files/usr/libexec/bazzite-tower-firstboot"
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
install -d "${tmp}/bin"
install -m 0644 /dev/stdin "${tmp}/passwd" <<'EOF'
root:x:0:0:root:/root:/bin/bash
alice:x:1000:1000:Alice:/home/alice:/bin/bash
EOF
install -m 0755 /dev/stdin "${tmp}/bin/getent" <<'EOF'
#!/usr/bin/env bash
case "$2" in kvm|libvirt|docker) exit 0 ;; *) exit 2 ;; esac
EOF
install -m 0755 /dev/stdin "${tmp}/bin/usermod" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${BAZZITE_TOWER_TEST_USERMOD_LOG}"
EOF

PATH="${tmp}/bin:${PATH}" \
BAZZITE_TOWER_PASSWD_FILE="${tmp}/passwd" \
BAZZITE_TOWER_GROUPS_MARKER="${tmp}/marker" \
BAZZITE_TOWER_USERMOD_BIN="${tmp}/bin/usermod" \
BAZZITE_TOWER_TEST_USERMOD_LOG="${tmp}/usermod.log" \
"${helper}" >/dev/null

[[ -f "${tmp}/marker" ]]
grep -qx -- '-aG kvm,libvirt alice' "${tmp}/usermod.log"
if grep -q docker "${tmp}/usermod.log"; then
    echo "firstboot unexpectedly requested Docker group membership" >&2
    exit 1
fi
echo "firstboot grants only kvm/libvirt: pass"
