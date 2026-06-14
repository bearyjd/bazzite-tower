<!-- Generated: 2026-06-14 | Files scanned: 8 | Token estimate: ~760 -->
# CI / CD

5 workflows + 2 test scripts + 1 diff filter. Full failure model:
[`../downstream-change-tracking.md`](../downstream-change-tracking.md).

## Workflows (`.github/workflows/`)

| Workflow | Triggers | Flow | Issue label |
|---|---|---|---|
| `build.yml` | push main (ignores README/docs/**), PR, Sun 06:00 UTC, dispatch | build → **smoke gate** (`tests/smoke.sh`, pre-push) → login → push GHCR → cosign sign by digest (if `SIGNING_SECRET`) | `ci-failure` |
| `boot-test.yml` | PR (build paths), Sun 07:00 UTC, dispatch | build → `podman run --systemd=always /sbin/init` → wait running/degraded → exec `tests/boot-check.sh` | `boot-test-failure` |
| `base-watch.yml` | daily 05:00 UTC, dispatch | pull base → `rpm -qa` manifest → `ci/base-diff.py` vs last-seen baseline in `docs/manifests/` (written on first run) → commit refreshed manifest | `base-bump` |
| `build-disk.yml` | dispatch (platform amd64/arm64), PR (disk.toml path) | bootc-image-builder → qcow2 disk image (rootfs=btrfs) → artifact or S3. anaconda-iso disabled: upstream BIB#1188 + bazzite#3418 | — |
| `build-iso.yml` | dispatch, Sun 08:00 UTC | `podman build installer/` payload (live session + Anaconda, Fedora-signed kernel for Secure Boot) → titanoboa → bootable ISO → checksum + cosign sign-blob → artifact or S3 | `iso-failure` |

The `installer/` payload + titanoboa contract is documented in
[iso-build.md](iso-build.md). `base-watch.yml` retries the base-image pull
before failing (transient GHCR 502s).

**Gate ordering** in `build.yml`: the smoke test runs *before* login/push, so a
broken image is never published (`:latest` stays last-good). Each gated workflow
opens — and later auto-closes — its labelled tracking issue.

## Test scripts (`tests/`)

- `smoke.sh` (154L) — offline, `podman run -i <img> bash -s <`. Asserts: qemu user resolves; 6 `virt*.socket` enabled; `libvirtd` masked; default-net symlink; polkit rule; wifi-guard/firstboot/docker enabled; **docker group exists**; `iptable_nat`; **looking-glass-client ujust recipe present**; all 6 `kargs.d` fragments (incl. SOF bypass, vfio-kvm, nvme); **SOF bypass karg + WirePlumber backoff**; **CPU power tuning (thermald + power-tuning svc/helper)**; **RAS** (rasdaemon enabled, mcelog masked, microcode_ctl); **smartd**; **swappiness=10 + baloofilerc**; **journald 500M cap**; **amdgpu blacklist**; **cockpit-machines + cockpit.socket**. Reports every failure, not just the first.
- `boot-check.sh` (69L) — runtime, inside the booted image. HARD = qemu user resolves, virtqemud/virtnetworkd active, `virsh -c qemu:///system` connects, wifi-guard active + not-failed, **no SOF `FW reported error: 9` / `failed widget list set up` in the boot journal**. SOFT (container limits) = NetworkManager, firstboot, docker.

## Diff filter (`ci/base-diff.py`)

Blast-radius regex over package NAME: `qemu* / libvirt* / edk2-ovmf / swtpm / virt-* /
NetworkManager* / iwd / wpa_supplicant / polkit* / systemd* / kernel* / bootc /
docker-ce* / containerd* / moby*`. Emits a markdown report + `GITHUB_OUTPUT` `changed`/`report`.

## Local mirror (Justfile)

`just smoke` = the build.yml gate. Also `just build`, `just build-qcow2`,
`just run-vm-qcow2`, `just spawn-vm`, `just build-iso-live` (payload + titanoboa
ISO), `just check`/`lint`/`format`.
