#!/usr/bin/bash
# Live-ISO payload build. Runs INSIDE the payload image build (privileged).
# Minimal first cut ported from github.com/ublue-os/titanoboa examples/bazzite,
# trimmed to: live session (KDE) + Anaconda installing bazzite-tower. UNVERIFIED.
set -exo pipefail
{ export PS4='+( ${BASH_SOURCE}:${LINENO} ): '; } 2>/dev/null

INSTALL_IMAGE="${INSTALL_IMAGE:-ghcr.io/bearyjd/bazzite-tower:latest}"

# /root is a symlink on these images; make sure its target exists.
mkdir -p "$(realpath /root)"
# bwrap (flatpak/dnf scriptlets) needs /proc/sys writable during the build.
mount -o remount,rw /proc/sys || true

# Embed the image to install so the live ISO can install fully offline.
podman pull "${INSTALL_IMAGE}"

# Live initramfs: add the dmsquash-live modules so the ISO can mount its
# squashfs as the live root. Without this the ISO boots but finds no root.
dnf install -y dracut-live
kernel="$(kernel-install list --json pretty | jq -r '.[] | select(.has_kernel == true) | .version')"
DRACUT_NO_XATTR=1 dracut -v --force --zstd --reproducible --no-hostonly \
    --add "dmsquash-live dmsquash-live-autooverlay" \
    "/usr/lib/modules/${kernel}/initramfs.img" "${kernel}"

# Live session scripts (KDE).
dnf install -y livesys-scripts
sed -i "s/^livesys_session=.*/livesys_session=kde/" /etc/sysconfig/livesys
systemctl enable livesys.service livesys-late.service

# Anaconda + a kickstart that installs bazzite-tower from the embedded image
# (containers-storage transport = offline). This is the actual install path.
dnf install -y --enable-repo=fedora-cisco-openh264 --allowerasing \
    anaconda-live firefox libblockdev-btrfs libblockdev-lvm libblockdev-dm
mkdir -p /var/lib/rpm-state  # Anaconda Web UI needs this
cat >>/usr/share/anaconda/interactive-defaults.ks <<EOF

ostreecontainer --url=${INSTALL_IMAGE} --transport=containers-storage --no-signature-verification
EOF

# ISO builder bits + the EFI layout titanoboa's build_iso.sh expects.
dnf install -y grub2-efi-x64-cdboot xorriso isomd5sum
mkdir -p /boot/efi
cp -av /usr/lib/efi/*/*/EFI /boot/efi/ || true
cp -v /boot/efi/EFI/fedora/grubx64.efi /boot/efi/EFI/BOOT/fbx64.efi || true

# UTC clock for the live session.
systemd-firstboot --timezone UTC || true

# The live root is a small tmpfs overlay; ostree install needs room in /var/tmp.
rm -rf /var/tmp
mkdir -p /var/tmp
cat >/etc/systemd/system/var-tmp.mount <<'EOF'
[Unit]
Description=Larger tmpfs for /var/tmp on the live system
[Mount]
What=tmpfs
Where=/var/tmp
Type=tmpfs
Options=size=50%,nr_inodes=1m
[Install]
WantedBy=local-fs.target
EOF
systemctl enable var-tmp.mount

# The ISO config titanoboa requires at this exact path.
mkdir -p /usr/lib/bootc-image-builder
cp /src/iso.yaml /usr/lib/bootc-image-builder/iso.yaml

dnf clean all || true
