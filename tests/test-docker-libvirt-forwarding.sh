#!/usr/bin/env bash
# Exercise Docker/libvirt forwarding discovery with mocked host commands.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
helper="${repo_root}/system_files/usr/local/libexec/docker-libvirt-forwarding"
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
install -d "${tmp}/bin"

install -m 0755 /dev/stdin "${tmp}/bin/virsh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == net-list ]]; then
    [[ "${MOCK_VIRSH_LIST_FAIL:-0}" != 1 ]] || exit 7
    case "${NAT_SCENARIO}" in
        default) printf 'default\n' ;;
        multi) printf 'default\nextra-nat\nnot-nat\n' ;;
        plus) printf 'plus-nat\n' ;;
    esac
    exit 0
fi
[[ "${MOCK_VIRSH_XML_FAIL:-}" != "$2" ]] || exit 8
case "$2" in
    default) printf '%s\n' "<network><forward mode='nat'/><bridge name='virbr0'/></network>" ;;
    extra-nat) printf '%s\n' "<network><forward mode='nat'/><bridge name='virbr42'/></network>" ;;
    plus-nat) printf '%s\n' "<network><forward mode='nat'/><bridge name='virbr+'/></network>" ;;
    not-nat) printf '%s\n' "<network><forward mode='route'/><bridge name='virbr99'/></network>" ;;
esac
EOF

install -m 0755 /dev/stdin "${tmp}/bin/ip" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == '-o route show default' ]]; then
    [[ "${MOCK_IP_ROUTE_FAIL:-0}" != 1 ]] || exit 9
    printf '%s\n' 'default via 192.0.2.1 dev wlp9s0f0 proto dhcp metric 600'
    exit 0
fi
case "$*" in
    *'dev virbr0 '*) [[ "${MOCK_IP_ADDRESS_FAIL:-0}" != 1 ]] || exit 10; printf '%s\n' '7: virbr0    inet 192.168.122.1/24 brd 192.168.122.255 scope global virbr0' ;;
    *'dev virbr42 '*) printf '%s\n' '8: virbr42   inet 192.168.42.1/24 brd 192.168.42.255 scope global virbr42' ;;
    *'dev virbr99 '*) printf '%s\n' '9: virbr99   inet 10.99.0.1/24 brd 10.99.0.255 scope global virbr99' ;;
esac
EOF

install -m 0755 /dev/stdin "${tmp}/bin/iptables" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
state=${MOCK_IPTABLES_STATE:?}
log=${MOCK_IPTABLES_LOG:?}
[[ "${1:-}" == -w ]] || exit 64
shift
operation=${1:-}
shift
[[ "${1:-}" == DOCKER-USER ]] || exit 64
shift
if [[ "${operation}" == -S ]]; then
    [[ "${MOCK_DOCKER_USER_AVAILABLE:-1}" == 1 ]]
    printf '%s\n' '-N DOCKER-USER'
    while IFS= read -r rule; do
        [[ -n "${rule}" ]] && printf '%s\n' "-A DOCKER-USER ${rule}"
    done < "${state}"
    exit
fi
if [[ "${operation}" == -I ]]; then
    [[ "${1:-}" == 1 ]] || exit 64
    shift
fi
rule="$*"
if [[ "${operation}" == -I ]]; then
    [[ "$(tail -n 1 "${log}" 2>/dev/null || true)" == "-C ${rule}" ]] || {
        echo "insert without an immediately preceding matching check" >&2
        exit 65
    }
fi
printf '%s %s\n' "${operation}" "${rule}" >> "${log}"
case "${operation}" in
    -C) grep -Fqx -- "${rule}" "${state}" ;;
    -I) grep -Fqx -- "${rule}" "${state}" || printf '%s\n' "${rule}" >> "${state}" ;;
    -D)
        [[ -z "${MOCK_IPTABLES_FAIL_DELETE:-}" || "${rule}" != *"${MOCK_IPTABLES_FAIL_DELETE}"* ]] || exit 66
        grep -Fqx -- "${rule}" "${state}"
        awk -v target="${rule}" '$0 != target' "${state}" > "${state}.next"
        mv "${state}.next" "${state}"
        ;;
    *) exit 64 ;;
esac
EOF

run_helper() {
    NAT_SCENARIO="$1" \
    VIRSH_BIN="${tmp}/bin/virsh" \
    IP_BIN="${tmp}/bin/ip" \
    IPTABLES_BIN="${tmp}/bin/iptables" \
    MOCK_IPTABLES_STATE="${tmp}/rules" \
    MOCK_IPTABLES_LOG="${tmp}/iptables.log" \
    DOCKER_LIBVIRT_FORWARDING_LOCK="${tmp}/lock" \
    "${helper}"
}

default_comment='bazzite-tower-libvirt-forwarding:virbr0:192.168.122.0/24:wlp9s0f0'
rule_outbound="-i virbr0 -o wlp9s0f0 -s 192.168.122.0/24 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -m comment --comment ${default_comment} -j ACCEPT"
rule_return="-i wlp9s0f0 -o virbr0 -d 192.168.122.0/24 -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment ${default_comment} -j ACCEPT"
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
run_helper default
grep -Fqx -- "${rule_outbound}" "${tmp}/rules"
grep -Fqx -- "${rule_return}" "${tmp}/rules"
[[ $(wc -l < "${tmp}/rules") -eq 2 ]]
run_helper default
[[ $(wc -l < "${tmp}/rules") -eq 2 ]]
[[ $(grep -c '^-I ' "${tmp}/iptables.log") -eq 2 ]]

# A later lifecycle trigger waits for the current reconciliation instead of
# being dropped; once the lock holder exits it applies the desired pair once.
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
touch "${tmp}/lock"
# shellcheck disable=SC2016 # The child shell expands its positional argument.
flock "${tmp}/lock" bash -c 'touch "$1"; sleep 0.2' _ "${tmp}/lock-held" &
lock_holder=$!
while [[ ! -e "${tmp}/lock-held" ]]; do sleep 0.01; done
run_helper default
wait "${lock_holder}"
grep -Fqx -- "${rule_outbound}" "${tmp}/rules"
grep -Fqx -- "${rule_return}" "${tmp}/rules"
[[ $(grep -c '^-I ' "${tmp}/iptables.log") -eq 2 ]]

: > "${tmp}/rules"
: > "${tmp}/iptables.log"
run_helper multi
grep -Fqx -- '-i virbr42 -o wlp9s0f0 -s 192.168.42.0/24 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -m comment --comment bazzite-tower-libvirt-forwarding:virbr42:192.168.42.0/24:wlp9s0f0 -j ACCEPT' "${tmp}/rules"
grep -Fqx -- '-i wlp9s0f0 -o virbr42 -d 192.168.42.0/24 -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment bazzite-tower-libvirt-forwarding:virbr42:192.168.42.0/24:wlp9s0f0 -j ACCEPT' "${tmp}/rules"
[[ $(wc -l < "${tmp}/rules") -eq 4 ]]
if grep -q virbr99 "${tmp}/rules"; then
    echo "non-NAT bridge unexpectedly received a forwarding rule" >&2
    exit 1
fi

# Reconciliation removes only stale rules carrying this helper's prefix: a
# stopped network, a changed bridge address, and a changed default uplink.
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
printf '%s\n' \
    '-i virbr0 -o wlp9s0f0 -s 192.168.123.0/24 -m comment --comment bazzite-tower-libvirt-forwarding:virbr0:192.168.123.0/24:wlp9s0f0 -j ACCEPT' \
    '-i virbr0 -o oldwan0 -s 192.168.122.0/24 -m comment --comment bazzite-tower-libvirt-forwarding:virbr0:192.168.122.0/24:oldwan0 -j ACCEPT' \
    '-i virbr13 -o wlp9s0f0 -s 192.168.13.0/24 -m comment --comment bazzite-tower-libvirt-forwarding:virbr13:192.168.13.0/24:wlp9s0f0 -j ACCEPT' \
    '-i unrelated0 -o unrelated1 -s 10.0.0.0/8 -j ACCEPT' > "${tmp}/rules"
run_helper default
grep -Fqx -- "${rule_outbound}" "${tmp}/rules"
grep -Fqx -- '-i unrelated0 -o unrelated1 -s 10.0.0.0/8 -j ACCEPT' "${tmp}/rules"
if grep -q 'oldwan0\|virbr13\|192.168.123.0/24' "${tmp}/rules"; then
    echo "stale helper-owned forwarding rules were retained" >&2
    exit 1
fi
[[ $(grep -c '^-D ' "${tmp}/iptables.log") -eq 3 ]]

# Listing a network or a live bridge address must fail loudly rather than be
# mistaken for an empty desired state (which would otherwise prune good rules).
for failure in virsh-list virsh-xml ip-address; do
    : > "${tmp}/rules"
    : > "${tmp}/iptables.log"
    case "${failure}" in
        virsh-list) MOCK_VIRSH_LIST_FAIL=1 ;;
        virsh-xml) MOCK_VIRSH_XML_FAIL=default ;;
        ip-address) MOCK_IP_ADDRESS_FAIL=1 ;;
    esac
    if NAT_SCENARIO=default \
        VIRSH_BIN="${tmp}/bin/virsh" IP_BIN="${tmp}/bin/ip" IPTABLES_BIN="${tmp}/bin/iptables" \
        MOCK_IPTABLES_STATE="${tmp}/rules" MOCK_IPTABLES_LOG="${tmp}/iptables.log" \
        MOCK_VIRSH_LIST_FAIL="${MOCK_VIRSH_LIST_FAIL:-0}" MOCK_VIRSH_XML_FAIL="${MOCK_VIRSH_XML_FAIL:-}" \
        MOCK_IP_ADDRESS_FAIL="${MOCK_IP_ADDRESS_FAIL:-0}" \
        DOCKER_LIBVIRT_FORWARDING_LOCK="${tmp}/lock" "${helper}" >/dev/null 2>&1; then
        echo "helper must fail on ${failure} discovery error" >&2
        exit 1
    fi
    unset MOCK_VIRSH_LIST_FAIL MOCK_VIRSH_XML_FAIL MOCK_IP_ADDRESS_FAIL
done

: > "${tmp}/rules"
: > "${tmp}/iptables.log"
if NAT_SCENARIO=default MOCK_DOCKER_USER_AVAILABLE=0 \
    VIRSH_BIN="${tmp}/bin/virsh" IP_BIN="${tmp}/bin/ip" IPTABLES_BIN="${tmp}/bin/iptables" \
    MOCK_IPTABLES_STATE="${tmp}/rules" MOCK_IPTABLES_LOG="${tmp}/iptables.log" \
    DOCKER_LIBVIRT_FORWARDING_LOCK="${tmp}/lock" "${helper}"; then
    echo "helper must fail when Docker has not created DOCKER-USER" >&2
    exit 1
fi
[[ ! -s "${tmp}/rules" ]]
# Production locking (no lock-path override) must not leak a writable fd 9 to
# children such as iptables/ip; the lock is a read-only fd of the helper itself.
install -m 0755 /dev/stdin "${tmp}/bin/iptables-fdprobe" <<'EOF2'
#!/usr/bin/env bash
flags=$(awk '/^flags:/ { print $2 }' /proc/self/fdinfo/9 2>/dev/null || true)
printf '%s\n' "${flags:-none}" >> "${MOCK_FD_LOG:?}"
exec "$(dirname "$0")/iptables" "$@"
EOF2
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
: > "${tmp}/fd.log"
NAT_SCENARIO=default VIRSH_BIN="${tmp}/bin/virsh" IP_BIN="${tmp}/bin/ip" \
    IPTABLES_BIN="${tmp}/bin/iptables-fdprobe" MOCK_FD_LOG="${tmp}/fd.log" \
    MOCK_IPTABLES_STATE="${tmp}/rules" MOCK_IPTABLES_LOG="${tmp}/iptables.log" "${helper}"
[[ -s "${tmp}/fd.log" ]]
while IFS= read -r flags; do
    [[ "${flags}" != none ]] || continue
    (( (8#${flags} & 3) == 0 )) || { echo "child inherited a writable fd 9" >&2; exit 1; }
done < "${tmp}/fd.log"
grep -qv '^none$' "${tmp}/fd.log" || { echo "fd 9 not inherited; probe is vacuous" >&2; exit 1; }

# Production lock (no override): the lock target is the helper file itself.
# Run a copy so the contention is on the copy, hold it, and require the helper
# to wait instead of applying rules until the holder releases.
helper_copy="${tmp}/helper-copy"
install -m 0755 "${helper}" "${helper_copy}"
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
rm -f "${tmp}/prod-held"
# shellcheck disable=SC2016 # The child shell expands its positional argument.
flock "${helper_copy}" bash -c 'touch "$1"; sleep 1' _ "${tmp}/prod-held" &
lock_holder=$!
while [[ ! -e "${tmp}/prod-held" ]]; do sleep 0.01; done
NAT_SCENARIO=default VIRSH_BIN="${tmp}/bin/virsh" IP_BIN="${tmp}/bin/ip" \
    IPTABLES_BIN="${tmp}/bin/iptables" \
    MOCK_IPTABLES_STATE="${tmp}/rules" MOCK_IPTABLES_LOG="${tmp}/iptables.log" "${helper_copy}" &
waiter=$!
sleep 0.3
kill -0 "${waiter}" 2>/dev/null || { echo "helper did not wait for the production lock" >&2; exit 1; }
[[ ! -s "${tmp}/rules" ]] || { echo "helper applied rules while the lock was held" >&2; exit 1; }
wait "${lock_holder}"
wait "${waiter}"
grep -Fqx -- "${rule_outbound}" "${tmp}/rules" || { echo "rules missing after lock release" >&2; exit 1; }
grep -Fqx -- "${rule_return}" "${tmp}/rules" || { echo "return rule missing after lock release" >&2; exit 1; }

# A wildcard-looking bridge name must never reach iptables.
: > "${tmp}/rules"
: > "${tmp}/iptables.log"
run_helper plus 2>"${tmp}/err.log"
[[ ! -s "${tmp}/rules" ]] || { echo "wildcard bridge produced rules" >&2; exit 1; }
grep -qF 'ignoring bridge with unexpected name' "${tmp}/err.log" || { echo "wildcard bridge not reported" >&2; exit 1; }

# Stale pruning: a valid stale pair is removed; helper-prefixed rules with a
# malformed comment shape are left alone and do not stop later deletions.
stale_ok='bazzite-tower-libvirt-forwarding:virbr7:192.168.7.0/24:wlp9s0f0'
stale_space='"bazzite-tower-libvirt-forwarding:virbr8:192.168.8.0/24:wlp9s0f0 extra"'
stale_shape='bazzite-tower-libvirt-forwarding:virbr9:not-a-cidr!:wlp9s0f0'
seed_stale() {
    {
        printf '%s\n' "-i virbr8 -o wlp9s0f0 -m comment --comment ${stale_space} -j ACCEPT"
        printf '%s\n' "-i virbr9 -o wlp9s0f0 -m comment --comment ${stale_shape} -j ACCEPT"
        printf '%s\n' "-i virbr7 -o wlp9s0f0 -s 192.168.7.0/24 -m comment --comment ${stale_ok} -j ACCEPT"
        printf '%s\n' "-i virbr6 -o wlp9s0f0 -s 192.168.6.0/24 -m comment --comment bazzite-tower-libvirt-forwarding:virbr6:192.168.6.0/24:wlp9s0f0 -j ACCEPT"
    } > "${tmp}/rules"
    : > "${tmp}/iptables.log"
}
seed_stale
run_helper default
grep -qF -- "${stale_space}" "${tmp}/rules" || { echo "malformed (space) comment rule was deleted" >&2; exit 1; }
grep -qF -- "${stale_shape}" "${tmp}/rules" || { echo "malformed (shape) comment rule was deleted" >&2; exit 1; }
! grep -qF -- ":virbr7:" "${tmp}/rules" || { echo "valid stale rule was not pruned" >&2; exit 1; }
! grep -qF -- ":virbr6:" "${tmp}/rules" || { echo "second valid stale rule was not pruned" >&2; exit 1; }
grep -Fqx -- "${rule_outbound}" "${tmp}/rules" || { echo "desired rule missing after pruning" >&2; exit 1; }

# One failing deletion is reported, later deletions still run, and the exit is 1.
seed_stale
rc=0
MOCK_IPTABLES_FAIL_DELETE=virbr7 run_helper default 2>"${tmp}/err.log" || rc=$?
[[ ${rc} -eq 1 ]] || { echo "failed deletion must exit 1 (got ${rc})" >&2; exit 1; }
grep -qF 'failed to delete stale rule' "${tmp}/err.log" || { echo "failed deletion not reported" >&2; exit 1; }
grep -qF -- ":virbr7:" "${tmp}/rules" || { echo "failing rule unexpectedly removed" >&2; exit 1; }
! grep -qF -- ":virbr6:" "${tmp}/rules" || { echo "deletion loop aborted after one failure" >&2; exit 1; }

echo "docker/libvirt forwarding mock contracts: pass"
