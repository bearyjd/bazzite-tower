#!/usr/bin/bash
# preinitramfs hook: swap the ublue/bazzite kernel for a vanilla Fedora-signed
# kernel so the live ISO boots under Secure Boot. shim only trusts Fedora/MS
# signed kernels; the ublue kernel needs the ublue MOK enrolled, which a fresh
# machine (or VM) lacks -> "bad shim signature" from grub's shim_lock verifier.
#
# Runs BEFORE the dracut regen in build.sh so the initramfs targets the new
# kernel. The INSTALLED system is unaffected: it deploys the full bazzite-tower
# image (with its own signed kernel + NVIDIA). Only the live/installer session
# runs on the Fedora kernel (NVIDIA falls back to nouveau there).
#
# Ported from github.com/ublue-os/titanoboa examples/bazzite. `--repo
# fedora,updates` also sidesteps the third-party repos whose file:// GPG keys
# break depsolve.
set -exo pipefail

kernel_pkgs=(
    kernel kernel-core kernel-devel kernel-devel-matched
    kernel-modules kernel-modules-core kernel-modules-extra
)
# Drop ublue versionlocks (no-op if the plugin/locks aren't present), then
# remove the kernel packages. No running kernel in a container build, so
# protect_running_kernel=False is safe.
dnf -y versionlock delete "${kernel_pkgs[@]}" || :
dnf --setopt=protect_running_kernel=False -y remove "${kernel_pkgs[@]}" || :
(cd /usr/lib/modules && rm -rf -- ./*)

# Install the stock Fedora kernel (signed by the Fedora/MS chain shim trusts).
dnf -y --repo fedora,updates --setopt=tsflags=noscripts install kernel kernel-core
kernel="$(find /usr/lib/modules -maxdepth 1 -type d -printf '%P\n' | grep .)"
depmod "${kernel}"

# nouveau firmware for the live session (proprietary NVIDIA modules went away
# with the bazzite kernel).
dnf install -yq nvidia-gpu-firmware || :
dnf clean all -yq
