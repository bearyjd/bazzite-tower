#!/usr/bin/env bash
set -euo pipefail

# ── Docker CE ─────────────────────────────────────────────────────────────────
# Mirror of https://download.docker.com/linux/fedora/docker-ce.repo with every
# section disabled. We flip docker-ce-stable on for the install transaction only
# via --enablerepo, so the repo file is inert at runtime (no background updates,
# no surprise upgrades). Bazzite ships podman-docker which owns /usr/bin/docker;
# remove it first so docker-ce can land cleanly.
#
# Supply chain: the signing key is NOT trusted via an in-repo gpgkey=https URL.
# It is downloaded once, must contain exactly one primary key whose full
# fingerprint equals DOCKER_GPG_FPR (Docker's documented release-key
# fingerprint), and only that verified key is installed locally; every repo
# section then points at the local file. A mismatch fails the build.
DOCKER_GPG_FPR=060A61C51B558A7F742B77AAC52FEB6B621E9F35
DOCKER_KEY_DEST=/etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

# shellcheck source=build_files/lib/verify-gpg-key.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/verify-gpg-key.sh"

docker_key=$(mktemp)
docker_key_verified=$(mktemp)
trap 'rm -rf "${docker_key}" "${docker_key_verified}"' EXIT

curl -fsSL --retry 3 --proto '=https' --proto-redir '=https' \
    -o "${docker_key}" https://download.docker.com/linux/fedora/gpg
if ! verify_gpg_key "${docker_key}" "${DOCKER_GPG_FPR}" "${docker_key_verified}"; then
    echo "30-docker-ce: Docker GPG key verification failed (pin ${DOCKER_GPG_FPR})" >&2
    exit 1
fi
install -Dm644 "${docker_key_verified}" "${DOCKER_KEY_DEST}"

install -Dm644 /dev/stdin /etc/yum.repos.d/docker-ce.repo <<'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-stable-debuginfo]
name=Docker CE Stable - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/stable
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-stable-source]
name=Docker CE Stable - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/stable
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-test]
name=Docker CE Test - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/test
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-test-debuginfo]
name=Docker CE Test - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/test
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-test-source]
name=Docker CE Test - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/test
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-nightly]
name=Docker CE Nightly - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-nightly-debuginfo]
name=Docker CE Nightly - Debuginfo $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/debug-$basearch/nightly
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce

[docker-ce-nightly-source]
name=Docker CE Nightly - Sources
baseurl=https://download.docker.com/linux/fedora/$releasever/source/nightly
enabled=0
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce
EOF

dnf remove -y podman-docker || true

dnf install -y --enablerepo=docker-ce-stable \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin \
    iptables
