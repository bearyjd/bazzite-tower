<!-- Generated: 2026-11-26 | Files scanned: 6 | Token estimate: ~700 -->
<!-- Targeted update: 2026-10-05 | OpenSnitch/Snitchwatch release boundaries; not a full regeneration -->
# Dependencies & External Surfaces

## Base image

`ghcr.io/ublue-os/bazzite-nvidia-open`, **default pinned to the dated tag
`44.20260825`** (kernel `7.2.0-ogc6.1`) — Bazzite KDE + NVIDIA open kernel
modules, F44+ — not `:stable`, for reproducibility. Carries the i915 Meteor
Lake s2idle-resume fix; proprietary `bazzite-nvidia` was used until
2026-08-28 but forked onto a kernel track that won't get it (see
`docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md`).
`:stable` is still built as the opt-in `:latest-kernel` tag. Swap the
Containerfile `FROM` → `bazzite-nvidia` for the proprietary driver. Base
provides (relied on, not installed here): the
`kmod-kvmfr` (present but blocked here by `modprobe.d/disable-kvmfr.conf`),
tailscale, distrobox, most of Cockpit, the KDE Plasma desktop (`kwin`,
`kscreenlocker`, `libplasma`, ...).

## Packages layered by build.sh

virt stack (qemu-kvm, libvirt*, virt-*, edk2-ovmf, guestfs-tools, spice-gtk3),
Docker CE, dev tooling (android-tools, ccache, flatpak-builder, podman-machine/tui,
rclone, restic, zsh), the hardware/health additions (**smartmontools**,
**cockpit** (no cockpit-machines: it pulls in libvirt-dbus, see RUNBOOK), **rasdaemon**, **microcode_ctl**, **thermald**),
plus owner-invoked **bpftool**, **sysstat**, **igt-gpu-tools** and **trace-cmd**
(r16.1 recipe additions, not yet built/installed; automatic collection/tracing off),
and the firewall selector (default **OpenSnitch** v1.8.0, pinned RPM extraction +
Snitchwatch config; disabled **Portmaster** VM spike behind
`FIREWALL_DAEMON=portmaster`, see `docs/research/portmaster-bootc-spike.md`).

## OpenSnitch and Snitchwatch

`build_files/build.d/95-firewall.sh` pins and SHA-256 verifies the upstream
OpenSnitch 1.8.0 RPM before extraction. It installs `libnetfilter_queue` and
`nftables` explicitly; extraction neither resolves dependencies nor registers
the daemon in the RPM database. The default user bridge and both GUI profiles remain per-user installs. The opt-in system candidate adds a pinned native bridge and immutable source/toolchain/license provenance. It separately compiles the reviewed upstream OpenSnitch a1353848 (1.9.0; legacy keeps the 1.8.0 RPM) plus a downstream queue-lifetime/NFT-ownership repair, retaining module locks, generated protocol hashes, Go/protoc/RPM inventory and license payloads. Default legacy builds retain the RPM binary and do not compile either native component; they do compile the per-kernel eBPF modules from OpenSnitch v1.8.0 (b404c4c6, the RPM release's commit).

October 4 VM tests used Snitchwatch `1def745`. October 5 follow-up used base
`2690109` plus reviewed uncommitted changes, source snapshot `851f3a`. Its clean
x86_64 GUI build used pinned KDE/Qt 6.9.3 and Rust 1.89.0, declared protoc 29.3
and mold 2.40.4, and 657 lockfile-verified crate inputs. These test artifacts are
not image-consumed release pins; guest installation reported KDE 6.9 as
end-of-life, so a supported SDK/runtime needs release validation. OpenSnitch's
grpc-go 1.32.0 path still requires
the relative Unix address workaround; default KDE startup, daemon shutdown
and release/image integration remain gated. A fresh candidate GUI release
build at `5c2b44a` now passed on supported KDE 6.11 / Qt 6.11.2 with Rust
1.98.1 and mold 2.42.0; actual default KDE behavior requires fresh VM proof.
The GUI crate remains 0.1.0, separate from native bridge 0.1.1. See [system bridge research](../research/snitchwatch-system-bridge.md).

Snitchwatch daemon build toolchain: 7 Fedora 45 RPMs pinned by sha256 in
`build_files/firewall/snitchwatch-system-daemon-pins.json` and fetched from Koji
(signed copies, key sha256 pinned); see [image-build](image-build.md).
Phase 2 separately pins three signed Koji bridge RPMs (rust, cargo,
rust-std-static 1.100.0-1.fc45) in `build_files/firewall/snitchwatch-system-pins.json`.
The shared toolchain helper runs after the byte-identical upstream installer
and before the Rust gate; the daemon retains its seven pins. Remaining live
toolchain and transitive dependencies, including LLVM, are not fully hermetic
(protobuf 33.5's libraries come from live `protobuf-cpp`/`abseil-cpp`).
Re-pin `EXPECTED_PINS_SHA256` in the respective bridge
`snitchwatch-system-verify.py` or daemon `snitchwatch-system-daemon-verify.py`
whenever its pins manifest changes, and rerun relevant tests.

eBPF module toolchain (Track A, system and legacy profiles; legacy also fetches the one pinned OpenSnitch v1.8.0 commit from GitHub in `ebpf-legacy-prep`): six signed Koji RPMs from srpm
`llvm21` 21.1.8-10.fc45 (clang21, clang21-libs, clang21-resource-filesystem,
llvm21, llvm21-libs, llvm21-filesystem) plus the per-profile OpenSnitch commit
and `ebpf_prog` tree sha256 in `build_files/firewall/snitchwatch-system-ebpf-pins.json`;
`make` and the remaining libraries are live. Headers are BASE_IMAGE's own
`kernel-devel` (digest-pinned base), never Fedora's. Move
`EXPECTED_EBPF_PINS_SHA256` in `snitchwatch-system-ebpf.py` with that file; the
system commit must equal the daemon `sourceCommit` and the bridge `submoduleCommit`
(`tests/test-snitchwatch-ebpf.py`, `tests/test-snitchwatch-ebpf-legacy.py`).

The r15 bridge candidate source is pinned at
`bfc76e6607574b354e1d3b1494798e7ea78d1685` (tree
`be719d27d61f9a1a02567b8673d02dcd53548085`, unchanged OpenSnitch gitlink
`b404c4c6316760fa7bc415509d3f8d747f7dc9cc`; r13/r14 used `bf15bb5`). Bridge
packaging input hashes (15, adding upstream's `opensnitch.service` drop-in and
daemon config) and the pins verifier are refreshed from that committed snapshot. Source and
workflow review/runtime acceptance remain publication gates; the candidate pin
confers no stable release authority.

## KDE Plasma package-family pin

`05-pin-kde-packages.sh` (runs first, before any other `dnf install` in
`build.d/`) appends an `exclude=` line to `/etc/dnf/dnf.conf`'s `[main]` section
(guarded — refuses to run if `[main]` isn't found, rather than appending blind)
for `kwin`/`kwin-libs`/`kwin-common`/`libplasma`/`kscreenlocker`/
`plasma-workspace*`/`plasma-desktop`/`kdecoration`/`kf6-kwindowsystem`, so none
of this build's own dnf transactions can touch them. Not a `dnf.conf.d/`
drop-in — this base runs **dnf5**, whose real drop-in directory is
`/etc/dnf/libdnf5.conf.d/`, not `/etc/dnf/dnf.conf.d/`; the first version of
this fix used the latter, silently did nothing, and was caught by CI's own
smoke gate (see the "Correction" note in
`docs/research/kwin-screenlocker-abi-2026-08-08/REPORT.md`). Root cause it
guards against: `dnf install` for an unrelated package can still resolve a
newer `kwin`/`libplasma` against live Fedora/COPR repo metadata at build time
without a matching `kscreenlocker` build existing yet, producing an
undefined-symbol crash in `kwin_wayland` at login (black screen, no login
prompt) — see `docs/research/kwin-screenlocker-abi-2026-07-26/` and
`-2026-08-08/`. Verified by two `tests/smoke.sh` checks: `kwin`/`kscreenlocker`
share the same major.minor, and the `exclude=` line is actually present in
`/etc/dnf/dnf.conf`.

## Package repos

| Repo | State | Use |
|---|---|---|
| Fedora + ublue/bazzite COPRs (base) | enabled | virt stack, dev tooling, health pkgs |
| `docker-ce` (download.docker.com) | `enabled=0` on disk | build-time only via `--enablerepo`; inert at runtime |

## Registries / external services

- **GHCR** — image publish + pull (the rebase target)
- **download.docker.com** — build-time only (Docker CE + gpg key; key fingerprint-pinned in `30-docker-ce.sh` via `build_files/lib/verify-gpg-key.sh`, installed to `/etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce`, repos use `gpgkey=file://`)
- **S3** (rclone, optional) — disk/ISO upload (`S3_*` secrets)
- **ArtifactHub** — image metadata/labels

## Signing

cosign — `cosign.pub` tracked; private key via mandatory `SIGNING_SECRET` for
default-branch image releases. Images are signed by digest, carry a signed SPDX
installed-RPM inventory attestation (not a whole-filesystem or language-package
SBOM) and GitHub provenance. The image merges a repository-scoped
`sigstoreSigned` policy plus a sigstore-attachment registry config. The global
policy default is `reject`; only unrelated Docker/Podman pulls retain an
empty-scope Docker compatibility fallback. Bootstrap a host's first verified
switch with `scripts/install-signature-policy.sh` and
`bootc switch --enforce-container-sigpolicy`.
`matchRepository` accepts any digest signed by this repository key, so it does
not prevent a signed rollback: rollback-sensitive installs should switch to a
reviewed immutable `@sha256:…` reference rather than a mutable tag.

## GitHub Actions (SHA-pinned, renovate-managed)

`actions/checkout`, `ublue-os/remove-unwanted-software`,
`redhat-actions/buildah-build` + `push-to-registry`, `docker/metadata-action` +
`login-action`, `sigstore/cosign-installer`,
`actions/attest-build-provenance`, `actions/github-script`,
`actions/upload-artifact`,
`ublue-os/titanoboa` (live-ISO build; pinned to `main`).

## Local-dev tooling

`just`, `podman`, bootc-image-builder (digest-pinned
`quay.io/centos-bootc/bootc-image-builder`),
qemu (run-vm), `python3` (base-diff.py), `shellcheck`/`shfmt` (`just lint`/`format`).

## ISO build (live/installer)

Extra **build-time** packages the `installer/` payload pulls (not in the OS image):
`dracut-live`, `livesys-scripts`, `anaconda-live`, `grub2-efi-x64-cdboot`,
`xorriso`, `isomd5sum`, and a **stock Fedora-signed `kernel`** (Secure Boot). See
[iso-build.md](iso-build.md).

## Automation

`renovate.json` — pins/updates Actions + base. No application dependency manifest
(this is not an app).
