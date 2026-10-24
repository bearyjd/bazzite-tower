<!-- Generated: 2026-08-08 | Files scanned: 8 | Token estimate: ~800 -->
<!-- Targeted update: 2026-10-07 | OpenSnitch patch-test workflow; not a full regeneration -->
# CI / CD

Workflows, highlighted test scripts and the base-change diff filter. Full failure model:
[`../downstream-change-tracking.md`](../downstream-change-tracking.md).

## Workflows (`.github/workflows/`)

| Workflow | Triggers | Flow | Issue label |
|---|---|---|---|
| `build.yml` | push main (ignores README/docs/**), PR, Sun 06:00 UTC, dispatch | PR-only **verify** retains all three contexts (`safe-pin`, `latest-kernel`, `snitchwatch-system`), credential-free actual-head checkout/tree proof and local smoke/runtime checks. Default-branch **release** publishes only opt-in `:latest-kernel` after build/test/signing/SPDX/provenance gates. A separate **notify** job reads each release leg's actual conclusion from the Actions API | `ci-failure-<variant>` |
| `base-refresh.yml` | daily, dispatch; `main` only | policy-pinned accepted product source + fresh immutable upstream base → read-only resolve/build/validate jobs → checkout-free publisher → verified signature, SPDX, build provenance, GitHub attestation and host signature policy → recheck current main policy hash immediately before public pointers. Legacy moves `:stable` and compatibility `:latest`; reviewed r16.1 system moves only `:snitchwatch-system`. No per-base owner approval | — |
| `r13-candidate.yml` | reviewed exact-head candidate branch push; optional dispatch after main registration | read-only approval/build/test/OCI validation jobs → checkout-free fixed candidate-package publisher → existing automated signing/SPDX/provenance and scoped destination policy verification; no stable tag input | — |
| `release.yml` (**Release Snitchwatch System**) | main dispatch, one `digest` input that must equal the digest hard-coded in the workflow | one checkout-free job in the `release` environment (owner attaches reviewer/main-only protection): verify candidate key signature + SPDX → `skopeo copy --all --preserve-digests` to a staging tag → `cosign sign` + re-attest SPDX → verify + host `policy.json` skopeo check → move only `:snitchwatch-system` → re-resolve and re-check. Staging tag is never deleted (GHCR deletes whole versions). No rebuild | — |
| `boot-test.yml` | PR (build paths), Sun 07:00 UTC, dispatch | actual PR-head SHA checkout + head/tree proof in read-only boot job → build → `podman run --systemd=always /sbin/init` → wait running/degraded → exec `tests/boot-check.sh` | `boot-test-failure` (separate non-PR notifier only; no candidate code or checkout) |
| `base-watch.yml` | daily 05:00 UTC, dispatch | pull `bazzite-nvidia-open:stable` → `rpm -qa` manifest → `ci/base-diff.py` vs last-seen baseline in `docs/manifests/` (written on first run) → commit refreshed manifest (and fail if every push retry fails) | `base-bump` |
| `base-bump.yml` | Sat 05:30 UTC, dispatch | needs `BUMP_TOKEN` secret (else no-op: GITHUB_TOKEN PRs don't trigger CI) → `ci/base-bump.sh` resolves `bazzite-nvidia-open:stable` digest, rewrites the candidate `Containerfile` pin (automatic refresh resolves its base separately) → branch `base-bump/<digest>` + PR; never builds or merges | — |
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

`base-refresh.yml` permits missing-stable initialization only through the
reviewed, API-verified run2/attempt1 recovery described below; other missing
baselines hold for independently reviewed recovery. It reads `.github/base-refresh-policy.json`: both product
profiles pin reviewed source `1c01f086f58bd33a5d7651254671e57723d45561` for
the r16.1 transition. Legacy retains `IMAGE_TAG=latest`; system retains
`IMAGE_TAG=snitchwatch-system`. Source SHA/tree/cleanliness and profile contracts
are checked before the build; independent validation checks image/RPM/SPDX
identity and actual Bazzite/kernel floors (`44.20261023` /
`7.2.9-ogc3.1.fc44`). The publisher fetches trusted code from the GitHub API at
the dispatched main SHA, with no source checkout. Policy staleness blocks tag
movement. Features still need owner runtime acceptance, protected fixed-digest
promotion and a subsequent reviewed policy update.

The r16.1 transition changes only the global source identity and exact system
release-baseline digest, retaining the profile schema and all native pins. The
only direct system baseline is
`sha256:3f9a0d238e69917a33a23710a53963e70b64526cc8e44f9d24787944dcca1f27`,
and only after successful repository-key signature verification. Old/arbitrary
digests require destination-bound signed refresh provenance and monotonic version
checks; missing system tags fail closed. Regression tests cover these boundaries
and source tree/timestamp substitution. Merge only after protected promotion
succeeds, independent destination signature/SPDX checks pass, and exact-head CI
and independent policy review pass. Host runtime acceptance remains unverified;
the owner authorized the CI-based release path.

All four refresh artifact consumers (legacy/system validators and publishers)
use SHA-pinned download-artifact v8.0.2, strict digest mismatch errors and explicit
extraction. Each requires the completed download-path output through a quoted
environment variable before validation/revalidation, trust setup or publication.
This carries over the candidate pipeline's proven large-artifact transport fix;
it adds no trust exception and changes no profile or job permission.

Refresh trust collection checks exact staged policy/key/registry bytes and active
key/registry copies. The active policy is an intentional merge with upstream
rules: require reject default, exact tower signing rule and no more-specific
tower scope, then bind its complete bytes/hash and independently re-extract it.
The single reviewed missing-stable recovery is server run2/attempt1 only, tied to
failed run `40062419690` at `d8407c60f74ceafbcbc2e333198c9fd23ce4e260`;
GitHub API metadata and complete attempt-1 jobs must prove both publishers were
skipped. Run3+, reruns, API failure and identity mismatches HOLD.

The r16.1 promoter binds candidate run `40118265705` to image digest
`sha256:3f9a0d238e69917a33a23710a53963e70b64526cc8e44f9d24787944dcca1f27` and
provenance source `1c01f086f58bd33a5d7651254671e57723d45561`. Merging only arms
this digest; owner runtime acceptance remains unverified. This fixed-digest feature-acceptance path retains the owner `release` environment
and all signature, attestation and policy gates; automatic base refresh uses its
separate policy-pinned path above. Before the r16.1 policy transition merges,
system refresh holds after promotion because the prior policy cannot accept that
digest. The transition advances source and exact baseline together, after the
promotion and independent destination checks pass; fixed-digest promotion does
not fabricate signed base-refresh provenance. Both profile contracts stay exact.

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
r13 candidate flow selects new system bridge/daemon features for protected
acceptance. Automatic base refresh uses the separately pinned accepted system
product; it does not accept candidate features.

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
and rejects absent/nonregular/wrong-format BPF object fixtures. It also executes
the RUNBOOK backup/trap/editor sequence with recording command doubles,
covering backup failures, editor failure, restoration retry and HUP/INT/TERM
without privileged host actions. The installed
reporting-only sampler is `bazzite-tower-snitchwatch-resources`; owner A/B tests
are documented in RUNBOOK and remain separate from CI. The built-image smoke
gate requires all three ELF BPF objects while retaining the allow/proc baseline;
no CI fixture establishes live kernel probe compatibility. Historical
`r13-immutable-ci.yml` stays unchanged to preserve its original publication proof.

## Candidate artifact consumption recovery

The two candidate consumers in `r13-candidate.yml` pin the official Node24
`download-artifact` v8.0.2 action (`9000827ccba6bdab643e8b6fd33ac0654aef8333`),
require archive digest mismatch to error and decompression to run, then require
its completion-only `download-path` output to equal the expected data directory.
The trusted candidate/publisher gates retain the exact nine-file inventory,
source/head/run identity, per-file hashes, installed manifests and trust policy.
Failures print bounded free-space and top-level name/type/size diagnostics only;
no file contents, recursion or symlink following. Inventory rejection includes
bounded escaped missing/extra names without accepting incomplete or extra data.

Candidate40110051327 built and uploaded a ZIP64 archive with the exact nine root
entries, but the consuming gate rejected its downloaded inventory. The old action
emitted no completion message/output in the completed log; a forced runtime
compatibility warning was observed. The precise downloader failure is unproven;
this recovery replaces its legacy runtime/client and explicitly checks completion.
It requires a fresh reviewed head/control and a new attempt-one run, never a rerun
or relaxation of the failed candidate's contract.
