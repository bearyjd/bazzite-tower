# shellcheck shell=bash
# Portmaster VM spike only.  Deliberately bypass upstream's installer, which
# downloads mutable modules at install time, and build the daemon from the exact
# audited source commit instead.  This variant is NOT enabled by default.
portmaster_version="2.2.1"
portmaster_commit="af0c60140ec4a5d7239aaf61bb3d81ac3c56e51b"
portmaster_source="/tmp/portmaster-${portmaster_commit}"

dnf install -y git golang iptables
git init "${portmaster_source}"
git -C "${portmaster_source}" remote add origin https://github.com/safing/portmaster.git
git -C "${portmaster_source}" fetch --depth 1 origin "${portmaster_commit}"
git -C "${portmaster_source}" checkout --detach "${portmaster_commit}"
test "$(git -C "${portmaster_source}" rev-parse HEAD)" = "${portmaster_commit}"

(
    cd "${portmaster_source}" || exit
    install -d -m 0755 /usr/libexec/portmaster
    # This base deliberately has no usable /root home. Point Go's otherwise
    # implicit root-owned caches at the writable build tmpfs instead.
    GOCACHE=/tmp/portmaster-go-build \
        GOMODCACHE=/tmp/portmaster-go-mod \
        CGO_ENABLED=0 go build \
        -ldflags="-X github.com/safing/portmaster/base/info.version=${portmaster_version} \
                  -X github.com/safing/portmaster/base/info.buildSource=bazzite-tower \
                  -X github.com/safing/portmaster/base/info.buildTime=2026-08-02T00:00:00Z" \
        -o /usr/libexec/portmaster/portmaster-core \
        ./cmds/portmaster-core
)
rm -rf "${portmaster_source}"

# A null unit in /usr (not /etc) is rollback-safe and prevents an accidental
# OpenSnitch start in this test image. Portmaster remains disabled until it is
# explicitly started inside the disposable VM.
ln -s /dev/null /usr/lib/systemd/system/opensnitch.service
printf '%s\n' portmaster > /usr/share/bazzite-tower/firewall-daemon
