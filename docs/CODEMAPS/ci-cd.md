<!-- Generated: 2026-05-31 | Files scanned: 7 | Token estimate: ~700 -->
# CI / CD

4 workflows + 2 test scripts + 1 diff filter. Full failure model:
[`../downstream-change-tracking.md`](../downstream-change-tracking.md).

## Workflows (`.github/workflows/`)

| Workflow | Triggers | Flow | Issue label |
|---|---|---|---|
| `build.yml` | push main (ignores README/docs/**), PR, Sun 06:00 UTC, dispatch | build → **smoke gate** (`tests/smoke.sh`, pre-push) → login → push GHCR → cosign sign by digest (if `SIGNING_SECRET`) | `ci-failure` |
| `boot-test.yml` | PR (build paths), Sun 07:00 UTC, dispatch | build → `podman run --systemd=always /sbin/init` → wait running/degraded → exec `tests/boot-check.sh` | `boot-test-failure` |
| `base-watch.yml` | daily 05:00 UTC, dispatch | pull base → `rpm -qa` manifest → `ci/base-diff.py` vs `docs/manifests/` → commit refreshed manifest | `base-bump` |
| `build-disk.yml` | dispatch (platform amd64/arm64), PR (disk_config paths) | bootc-image-builder → qcow2 + anaconda-iso → artifact or S3 | — |

**Gate ordering** in `build.yml`: the smoke test runs *before* login/push, so a
broken image is never published (`:latest` stays last-good). Each gated workflow
opens — and later auto-closes — its labelled tracking issue.

## Test scripts (`tests/`)

- `smoke.sh` (96L) — offline, `podman run -i <img> bash -s <`: asserts qemu user resolves, 6 `virt*.socket` enabled, `libvirtd` masked, default-net symlink, polkit rule, wifi-guard/firstboot/docker enabled, `iptable_nat`, IOMMU kargs present. Reports every failure, not just the first.
- `boot-check.sh` (59L) — runtime, inside the booted image. HARD = qemu user resolves, virtqemud/virtnetworkd active, `virsh -c qemu:///system` connects, wifi-guard active + not-failed. SOFT (container limits) = NetworkManager, firstboot, docker.

## Diff filter (`ci/base-diff.py`)

Blast-radius regex over package NAME: `qemu* / libvirt* / edk2-ovmf / swtpm / virt-* /
NetworkManager* / iwd / wpa_supplicant / polkit* / systemd* / kernel* / bootc /
docker-ce* / containerd* / moby*`. Emits a markdown report + `GITHUB_OUTPUT` `changed`/`report`.

## Local mirror (Justfile)

`just smoke` = the build.yml gate. Also `just build`, `just build-qcow2`,
`just run-vm-qcow2`, `just spawn-vm`, `just check`/`lint`/`format`.
