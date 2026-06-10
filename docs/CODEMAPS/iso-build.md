<!-- Generated: 2026-06-10 | Files scanned: 5 | Token estimate: ~620 -->
# Live / Installer ISO

A **separate** build from the installed OS image. Produces a bootable live KDE
session + Anaconda that installs `bazzite-tower`. The plain image can't be an ISO
directly (it lacks the live/installer layer), so a *payload image* is built first.

```
ghcr.io/bearyjd/bazzite-tower:latest          (the installed OS image)
        │  FROM, in installer/Containerfile
        ▼
installer/src/build.sh  (live session + Anaconda + dmsquash-live initramfs)
        ▼
localhost/bazzite-tower-payload:latest         (satisfies titanoboa's ISO contract)
        │  ublue-os/titanoboa action
        ▼
bootable ISO  → checksum + cosign sign-blob → artifact / S3   (Secure Boot OK)
```

## `installer/` (the payload builder)

- `Containerfile` — `FROM ${BASE_IMAGE:-…/bazzite-tower:latest}`, `COPY ./src /`,
  `RUN /src/build.sh` (privileged: needs `--cap-add sys_admin`, label=disable).
- `src/build.sh` (78L) — runs inside the payload build:
  1. `podman pull` the install image → embedded, so the ISO installs **offline**
  2. run the preinitramfs hook (kernel swap, below) **before** dracut regen
  3. `dracut … --add "dmsquash-live dmsquash-live-autooverlay"` — live root from squashfs
  4. `livesys-scripts`, set `livesys_session=kde`, enable `livesys{,-late}.service`
  5. `anaconda-live` + kickstart appending `ostreecontainer --transport=containers-storage`
     (offline install of the embedded image)
  6. `grub2-efi-x64-cdboot xorriso isomd5sum` + EFI layout titanoboa expects
  7. `var-tmp.mount` (50% tmpfs) — ostree install needs room on the live overlay
  8. copy `iso.yaml` → `/usr/lib/bootc-image-builder/iso.yaml` (titanoboa reads it here)
- `src/titanoboa_hook_preinitramfs.sh` (36L) — **Secure Boot fix**: removes the
  ublue/bazzite kernel and installs the stock **Fedora-signed** kernel
  (`--repo fedora,updates`) so shim trusts it on a machine without the ublue MOK.
  Live session falls back to nouveau; the **installed** system keeps the full
  bazzite-tower signed kernel + NVIDIA (install path is unaffected).
- `src/iso.yaml` (10L) — titanoboa ISO config: `.label` = `bazzite-tower-Live`
  (must match `root=live:CDLABEL=` in the grub entry) + `.grub2` timeout/entries.

## CI

`build-iso.yml` (dispatch, Sun 08:00 UTC) builds the payload then runs the
`ublue-os/titanoboa` action (pinned to `main`; mid-revamp, no tagged release).
On failure it opens the `iso-failure` issue. See [ci-cd.md](ci-cd.md).

## Status

Live ISO **verified booting to the KDE desktop under enforcing Secure Boot**
(commit `1962fcd`). Ported/trimmed from `ublue-os/titanoboa` `examples/bazzite`
(upstream marks it "not for production"); bazzite branding / bootloader-restore /
steam-deck polish intentionally omitted.

## Local mirror

`just build-iso-live` builds the payload + ISO locally. (`just build-iso` is a
different, bootc-image-builder anaconda path using `disk_config/iso-kde.toml` —
the CI ISO uses titanoboa, not that.)
