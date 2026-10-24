<!-- Generated: 2026-08-08 | Files scanned: 8 | Token estimate: ~800 -->
<!-- Targeted update: 2026-10-07 | OpenSnitch patch-test workflow; not a full regeneration -->
# CI / CD

8 workflows + highlighted test scripts + 1 diff filter. Full failure model:
[`../downstream-change-tracking.md`](../downstream-change-tracking.md).

## Workflows (`.github/workflows/`)

| Workflow | Triggers | Flow | Issue label |
|---|---|---|---|
| `build.yml` | push main (ignores README/docs/**), PR, Sun 06:00 UTC, dispatch | Two mutually exclusive jobs: PR-only **verify** has `contents: read`, credential-free actual PR-head SHA checkout with checked-out head/tree proof, then builds/smokes/runtime-tests its own local legacy safe-pin/latest-kernel candidates and opt-in `verify-snitchwatch-system`; default-branch **release** publishes two legacy variants (`safe-pin`→`:latest`, opt-in `latest-kernel`); `snitchwatch-system` is held on push/schedule/dispatch and only accepted-digest promotion may move it and independently rebuilds/retests, queries the exact local candidate's RPM database into a deterministic SPDX installed-RPM inventory, pushes a unique candidate digest, requires cosign+signed SPDX+GitHub provenance verification, then promotes that unchanged digest to public tags. `fail-fast: false` — one leg failing never cancels the others. A separate **notify** job (`needs: release`, `if: always()`) reads each leg's actual conclusion from the Actions API and syncs its tracking issue — kept out of `release`'s own steps so a leg getting killed mid-step (not just failing cleanly) can't silently skip the notification | `ci-failure-<variant>` (release only; per-leg label, so one leg's success never auto-closes the other's issue) |
| `r13-candidate.yml` | reviewed exact-head candidate branch push; optional dispatch after main registration | read-only approval/build/test/OCI validation jobs → checkout-free fixed candidate-package publisher → existing automated signing/SPDX/provenance and scoped destination policy verification; no stable tag input | — |
| `release.yml` (**Release Snitchwatch System**) | main dispatch, one `digest` input that must equal the digest hard-coded in the workflow | one checkout-free job in the `release` environment (owner attaches reviewer/main-only protection): verify candidate key signature + SPDX → `skopeo copy --all --preserve-digests` to a staging tag → `cosign sign` + re-attest SPDX → verify + host `policy.json` skopeo check → move only `:snitchwatch-system` → re-resolve and re-check. Staging tag is never deleted (GHCR deletes whole versions). No rebuild | — |
| `boot-test.yml` | PR (build paths), Sun 07:00 UTC, dispatch | actual PR-head SHA checkout + head/tree proof in read-only boot job → build → `podman run --systemd=always /sbin/init` → wait running/degraded → exec `tests/boot-check.sh` | `boot-test-failure` (separate non-PR notifier only; no candidate code or checkout) |
| `base-watch.yml` | daily 05:00 UTC, dispatch | pull `bazzite-nvidia-open:stable` → `rpm -qa` manifest → `ci/base-diff.py` vs last-seen baseline in `docs/manifests/` (written on first run) → commit refreshed manifest (and fail if every push retry fails) | `base-bump` |
| `base-bump.yml` | Sat 05:30 UTC, dispatch | needs `BUMP_TOKEN` secret (else no-op: GITHUB_TOKEN PRs don't trigger CI) → `ci/base-bump.sh` resolves `bazzite-nvidia-open:stable` digest, rewrites the `Containerfile` pin → branch `base-bump/<digest>` + PR; never builds or merges | — |
| `build-disk.yml` | dispatch (platform amd64/arm64), PR (disk.toml path) | resolve `:latest` once to an immutable digest → verify it with `cosign.pub` → pass that digest to bootc-image-builder → qcow2 disk image (rootfs=btrfs) → artifact or S3. anaconda-iso disabled: upstream BIB#1188 + bazzite#3418 | — |
| `snitchwatch-daemon-patch.yml` | PR + push main (paths: `build_files/firewall/snitchwatch-system-daemon-*`, its script, itself), dispatch | `tests/test-snitchwatch-daemon-patch.sh`: pinned Fedora builder fetches the pinned, signature-verified Koji toolchain RPMs (helper `fetch` mode) plus live dependency RPMs, applies the pinned OpenSnitch patch to upstream `b404c4c` via the factory's check-source gates, then a `--network=none` container runs gofmt (patched files), `go vet` (netfilter/nftables in full; root/ui without the three analyzers upstream code trips), `go test -race` on root/netfilter/nftables/ui (three upstream ui race failures skipped by name; upstream's privileged nftables tests skip) and a plain `go test` pass. Not required (path-filtered). No image build | — |
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

The r15.1 promoter binds candidate run `40050864799` to image digest
`sha256:df4ec1239abae0cbe1d4d4e27bce85d54f17e2b5155e431c2727c724aa879bf7` and
provenance source `91908f11eba496cde806298f2d0cbe332213192d`. Owner runtime
acceptance remains **UNVERIFIED**; preparing this binding does not authorize
stable publication. The owner release environment and all signature, attestation
and policy gates remain mandatory.

All stable OCI workflows share `bazzite-tower-stable-writers` with cancellation disabled. The promoter's runtime evidence and review are recorded in the PR and release notes, not workflow variables; its steps are in [r13-publication.md](../r13-publication.md), which also covers the candidate trust bootstrap and artifact identities. `ci/r13-publication.py` is the read-only gate and `ci/r13-candidate-data.py` captures installed/OCI/RPM identity only in the credential-free builder. The primary-reviewed control store starts with null authority. Final tower heads first receive independent review and actual-head green PR CI on a separate review branch; only then may the primary record variables and fast-forward the existing fixed candidate branch to that exact tested head. Same-head pushes and detached dispatch do not supply fresh verify/artifact CI; no force/delete/main-registration shortcut. The full sequence is in the publication document.

The candidate collector creates its extraction container with an explicit
`/usr/bin/true` command, supporting bootc images without CMD/ENTRYPOINT. It
never starts that container and removes it in `finally`; the separate RPM
inventory run and digest-preserving OCI export are unchanged. Full collector
regressions execute the real artifact validation and SPDX generation with
offline Podman/Skopeo fixtures, including removal after collection failures.

## Test scripts (`tests/`)

- `test-r13-publication.py` — hash/ref/target/source/profile/receipt and OCI tamper refusals, privilege boundaries, automatic system hold, and promoter invariants (dispatch-only, main+repo guard, fixed digest, `release` environment, no checkout, no staging-tag delete, SHA-pinned actions, preserve-digests, tag moves last and only `:snitchwatch-system`; trust-fetch/SPDX steps run against fake `gh`/`cosign`); actual PR-head proof rejects synthetic-merge/missing identities and boot PR tokens cannot write issues. Offline fixtures only.

- `test-snitchwatch-gate.py` — offline wrapper reporting, failure propagation,
  prerequisites, network isolation and timeout contracts; both image
  validation jobs run it. `just snitchwatch-gate` snapshots exact inputs and
  retains per-check logs/hashes; offline PASS leaves runtime UNVERIFIED.

- `smoke.sh` — offline, `podman run -i <img> bash -s <`. Asserts the virtualisation and monitoring intent plus Docker/Cockpit/Waydroid disabled by default, a baked Docker group without user membership, Cockpit's loopback drop-in, and the reporting-only health helper. It retains the existing firewall, kernel-argument, firmware, and desktop contracts; reports every failure, not just the first.
- `boot-check.sh` — runtime, inside the booted image. HARD = qemu user resolves, virtqemud/virtnetworkd active, `virsh -c qemu:///system` connects, wifi-guard active + not-failed, optional Docker/Cockpit/Waydroid services disabled, and no SOF storm in the boot journal. SOFT (container limits) = NetworkManager and firstboot.
- `test-rpm-inventory-spdx.sh` — fixture contract for the standard-library SPDX generator: deterministic ordering and valid EVRA/SPDX structure, while malformed, empty, and duplicate RPM inventories fail closed.
- `test-opensnitch-readiness.sh` — behavioral legacy readiness/dispatch tests. `test-snitchwatch-system.sh` covers read-only system preflight and transactional migration, refusal and rollback with fixtures; `test-snitchwatch-image-build.py` covers selector/refusal and immutable artifact/overlay provenance. `test-snitchwatch-image-build-daemon.py` checks the separate patched-daemon source/patch/build inventory and actual installed-binary identity. Both verify/release jobs run these checks. `test-snitchwatch-daemon-patch.sh` (own workflow, above) is the only place the patch's Go and C harness tests run. None of these checks exercise a GUI or prove NFQUEUE/SELinux behavior.
- `test-docker-libvirt-forwarding.sh` — hard mocked contract: exact `virbr0` `192.168.122.0/24` / `wlp9s0f0` stateful tagged rule pair, multi-NAT discovery, idempotence, discovery failures, and safe stale-rule pruning that leaves unowned rules untouched. `test-docker-libvirt-forwarding-hooks.sh` verifies the trigger contracts (libvirt path unit + service, no libvirt hook, NetworkManager dispatcher, Docker drop-in). `test-docker-libvirt-forwarding-integration.sh` is a separate privileged, opt-in host probe because nested Docker firewall support is runner-dependent.

## Snitchwatch VM evidence scope

The [October 4 system-bridge validation](../research/snitchwatch-system-bridge.md)
is a manual disposable-VM result, separate from these image release gates.
It proved real GUI decisions, authorization, token rotation and headless startup
with SELinux enforcing using a guest-only bridge install and repaired Flatpak.
October 5 follow-up also passed an isolated clean source build of the corrected
system manifest, bounded request cleanup and conditional rendered-GUI tests.
Default KDE startup, daemon shutdown and runtime lifecycle remain gates; these
results do not establish immutable image installation or an automated image
release gate. The opt-in system candidate adds immutable manifest,
native loader, named-account, socket/token and resolved-unit checks. The
separate patched-daemon verifier checks the installed executable even when
rootless boot containers cannot start NFQUEUE; an active daemon additionally
requires the reviewed process hash, arguments and root UID. Fresh
enforcing-SELinux image VM evidence remains a separate gate. Default release
variants retain legacy TCP `127.0.0.1:50051`/`allow`; the opt-in
r13 candidate flow selects the system bridge and patched daemon; the system stable tag stays held until accepted-digest promotion.

## Diff filter (`ci/base-diff.py`)

Blast-radius regex over package NAME: `qemu* / libvirt* / edk2-ovmf / swtpm / virt-* /
NetworkManager* / iwd / wpa_supplicant / polkit* / systemd* / kernel* / bootc /
docker-ce* / containerd* / moby*`. Emits a markdown report + `GITHUB_OUTPUT` `changed`/`report`.

## Local mirror (Justfile)

`just build-snitchwatch-system` creates the local opt-in native system candidate;
`just build-snitchwatch-system-vm` additionally sets VM_GATE_SSH for the disposable
SSH gate. Neither recipe publishes an image or installs a GUI.

`just smoke` = the build.yml gate. Also `just build`, `just build-qcow2`,
`just run-vm-qcow2`, `just spawn-vm`, `just build-iso-live` (payload + titanoboa
ISO), `just check`/`lint`/`format`.

The candidate collector exports the current user's containers-storage image
through `podman unshare skopeo copy --all --preserve-digests`. This uses the
existing Podman user namespace and image store, without sudo or runner security
changes. The full collector regression models direct Skopeo unshare refusal and
checks namespace-export failure cleanup. Actual runner success requires fresh CI.
See [Podman unshare](https://docs.podman.io/en/latest/markdown/podman-unshare.1.html).

## OpenSnitch resource and eBPF packaging fixtures

The source-contract gates in `build.yml` (legacy and system candidate) and
`r13-candidate.yml` run `python3 tests/test-snitchwatch-resources.py` alongside
the daemon/image fixtures. It checks interval CPU accounting using monotonic
elapsed time, RSS/thread series, identity changes, missing/permission/timeouts,
and rejects absent/nonregular/wrong-format BPF object fixtures. The installed
reporting-only sampler is `bazzite-tower-snitchwatch-resources`; owner A/B tests
are documented in RUNBOOK and remain separate from CI. The built-image smoke
gate requires all three ELF BPF objects while retaining the allow/proc baseline;
no CI fixture establishes live kernel probe compatibility. Historical
`r13-immutable-ci.yml` stays unchanged to preserve its original publication proof.
