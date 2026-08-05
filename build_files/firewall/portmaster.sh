# shellcheck shell=bash
# Portmaster VM spike only.  Deliberately bypass upstream's installer, which
# downloads mutable modules at install time, and build the daemon from the exact
# audited source commit instead.  This variant is NOT enabled by default.
portmaster_version="2.2.1"
portmaster_commit="af0c60140ec4a5d7239aaf61bb3d81ac3c56e51b"
portmaster_source="/tmp/portmaster-${portmaster_commit}"

# Record what the base already shipped so the cleanup below only removes what
# this script added.  Bazzite is a desktop base and may already carry git as a
# dependency of something else; an unconditional `dnf remove git` would cascade
# into its dependents.  iptables is never removed — recover-iptables shells out
# to it at runtime.
base_had_golang=no
base_had_git=no
if rpm -q golang > /dev/null 2>&1; then base_had_golang=yes; fi
if rpm -q git > /dev/null 2>&1; then base_had_git=yes; fi

dnf install -y git golang iptables
git init "${portmaster_source}"
git -C "${portmaster_source}" remote add origin https://github.com/safing/portmaster.git
git -C "${portmaster_source}" fetch --depth 1 origin "${portmaster_commit}"
git -C "${portmaster_source}" checkout --detach "${portmaster_commit}"
test "$(git -C "${portmaster_source}" rev-parse HEAD)" = "${portmaster_commit}"

(
    cd "${portmaster_source}" || exit
    install -d -m 0755 /usr/libexec/portmaster
    # This base deliberately has no usable /root home, so Go's caches need an
    # explicit home. /var/cache is a `--mount=type=cache` in the Containerfile
    # and /tmp is a tmpfs, so caching here survives between local rebuilds
    # while /tmp would throw the whole build away each time. The spike needs
    # several VM iterations; each one would otherwise recompile from zero.
    GOCACHE=/var/cache/portmaster-go-build \
        GOMODCACHE=/var/cache/portmaster-go-mod \
        CGO_ENABLED=0 go build \
        -ldflags="-X github.com/safing/portmaster/base/info.version=${portmaster_version} \
                  -X github.com/safing/portmaster/base/info.buildSource=bazzite-tower \
                  -X github.com/safing/portmaster/base/info.buildTime=2026-08-02T00:00:00Z" \
        -o /usr/libexec/portmaster/portmaster-core \
        ./cmds/portmaster-core
)
rm -rf "${portmaster_source}"

# A Go compiler on a firewall image is avoidable attack surface, and roughly
# 500 MB of it.  The binary is static (CGO_ENABLED=0), so nothing built here is
# needed at runtime.  `|| true` mirrors build.sh's podman-docker removal: never
# fail the build if the removal turns out to be impossible.
cleanup_pkgs=()
if [[ "${base_had_golang}" == "no" ]]; then cleanup_pkgs+=(golang); fi
if [[ "${base_had_git}" == "no" ]]; then cleanup_pkgs+=(git); fi
if [[ ${#cleanup_pkgs[@]} -gt 0 ]]; then
    dnf remove -y "${cleanup_pkgs[@]}" || true
fi

# A null unit in /usr (not /etc) is rollback-safe and prevents an accidental
# OpenSnitch start in this test image. Portmaster remains disabled until it is
# explicitly started inside the disposable VM.
ln -s /dev/null /usr/lib/systemd/system/opensnitch.service
printf '%s\n' portmaster > /usr/share/bazzite-tower/firewall-daemon
