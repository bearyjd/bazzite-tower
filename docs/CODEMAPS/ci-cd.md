<!-- Generated: 2026-08-08 | Files scanned: 8 | Token estimate: ~800 -->
# CI / CD

5 workflows + 4 highlighted test scripts + 1 diff filter. Full failure model:
[`../downstream-change-tracking.md`](../downstream-change-tracking.md).

## Workflows (`.github/workflows/`)

| Workflow | Triggers | Flow | Issue label |
|---|---|---|---|
| `build.yml` | push main (ignores README/docs/**), PR, Sun 06:00 UTC, dispatch | Two mutually exclusive matrices: PR-only **verify** has `contents: read`, credential-free SHA checkout, then builds/smokes/runtime-tests its own local candidates; default-branch **release** independently rebuilds/retests, queries the exact local candidate's RPM database into a deterministic SPDX installed-RPM inventory, pushes a unique candidate digest, requires cosign+signed SPDX+GitHub provenance verification, then promotes that unchanged digest to public tags. `fail-fast: false` — one leg failing never cancels the other. A separate **notify** job (`needs: release`, `if: always()`) reads each leg's actual conclusion from the Actions API and syncs its tracking issue — kept out of `release`'s own steps so a leg getting killed mid-step (not just failing cleanly) can't silently skip the notification | `ci-failure-<variant>` (release only; per-leg label, so one leg's success never auto-closes the other's issue) |
| `boot-test.yml` | PR (build paths), Sun 07:00 UTC, dispatch | build → `podman run --systemd=always /sbin/init` → wait running/degraded → exec `tests/boot-check.sh` | `boot-test-failure` |
| `base-watch.yml` | daily 05:00 UTC, dispatch | pull `bazzite-nvidia-open:stable` → `rpm -qa` manifest → `ci/base-diff.py` vs last-seen baseline in `docs/manifests/` (written on first run) → commit refreshed manifest (and fail if every push retry fails) | `base-bump` |
| `build-disk.yml` | dispatch (platform amd64/arm64), PR (disk.toml path) | resolve `:latest` once to an immutable digest → verify it with `cosign.pub` → pass that digest to bootc-image-builder → qcow2 disk image (rootfs=btrfs) → artifact or S3. anaconda-iso disabled: upstream BIB#1188 + bazzite#3418 | — |
| `build-iso.yml` | dispatch, Sun 08:00 UTC | `podman build installer/` payload (live session + Anaconda, Fedora-signed kernel for Secure Boot) → titanoboa → bootable ISO → checksum + cosign sign-blob → artifact or S3 | `iso-failure` |

The `installer/` payload + titanoboa contract is documented in
[iso-build.md](iso-build.md). `base-watch.yml` retries the base-image pull
before failing (transient GHCR 502s). `build-iso.yml` pins rootful podman to
**native kernel overlayfs** (job-global `/etc/containers/storage.conf`) before
any podman use: the ubuntu-24.04 runner's podman bundle rework switched root
storage to fuse-overlayfs, which EINVALs the nested `podman pull`'s literal
`.wh.*` whiteout writes inside the payload build container (issue #47, PR #48).

**Gate ordering** in `build.yml`: both jobs build and run smoke/runtime-systemd
checks against their own exact local candidates. Before any candidate push, the
release job queries that immutable local tag's RPM database and creates a
deterministic SPDX 2.3 installed-RPM inventory; it does not re-pull the
candidate for SBOM generation. This is intentionally not a whole-filesystem or
language-dependency catalog. It then pushes the same local candidate, signs and
verifies the RPM-inventory SBOM against its remote digest, verifies GitHub
provenance, and only then promotes that unchanged digest to public tags. A
broken image therefore never advances a published variant (each tag stays
last-good independently). The separate `notify` job opens — and later
auto-closes — each leg's labelled tracking issue, independently of whether
`release` finished cleanly or was cancelled/killed partway through.

## Test scripts (`tests/`)

- `smoke.sh` — offline, `podman run -i <img> bash -s <`. Asserts the virtualisation and monitoring intent plus Docker/Cockpit/Waydroid disabled by default, a baked Docker group without user membership, Cockpit's loopback drop-in, and the reporting-only health helper. It retains the existing firewall, kernel-argument, firmware, and desktop contracts; reports every failure, not just the first.
- `boot-check.sh` — runtime, inside the booted image. HARD = qemu user resolves, virtqemud/virtnetworkd active, `virsh -c qemu:///system` connects, wifi-guard active + not-failed, optional Docker/Cockpit/Waydroid services disabled, and no SOF storm in the boot journal. SOFT (container limits) = NetworkManager and firstboot.
- `test-rpm-inventory-spdx.sh` — fixture contract for the standard-library SPDX generator: deterministic ordering and valid EVRA/SPDX structure, while malformed, empty, and duplicate RPM inventories fail closed.
- `test-docker-libvirt-forwarding.sh` — hard mocked contract: exact `virbr0` `192.168.122.0/24` / `wlp9s0f0` stateful tagged rule pair, multi-NAT discovery, idempotence, discovery failures, and safe stale-rule pruning that leaves unowned rules untouched. `test-docker-libvirt-forwarding-hooks.sh` verifies the asynchronous libvirt and NetworkManager route/VPN/reapply trigger contracts. `test-docker-libvirt-forwarding-integration.sh` is a separate privileged, opt-in host probe because nested Docker firewall support is runner-dependent.

## Diff filter (`ci/base-diff.py`)

Blast-radius regex over package NAME: `qemu* / libvirt* / edk2-ovmf / swtpm / virt-* /
NetworkManager* / iwd / wpa_supplicant / polkit* / systemd* / kernel* / bootc /
docker-ce* / containerd* / moby*`. Emits a markdown report + `GITHUB_OUTPUT` `changed`/`report`.

## Local mirror (Justfile)

`just smoke` = the build.yml gate. Also `just build`, `just build-qcow2`,
`just run-vm-qcow2`, `just spawn-vm`, `just build-iso-live` (payload + titanoboa
ISO), `just check`/`lint`/`format`.
