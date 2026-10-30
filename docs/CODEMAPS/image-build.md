<!-- Generated: 2026-08-08 | Files scanned: 3 | Token estimate: ~850 -->
<!-- Targeted update: 2026-10-05 | Snitchwatch image-consumer gate; not a full regeneration -->
# Image Build Pipeline

`Containerfile` → `build_files/build.sh` (runs inside the build, `set -euo pipefail`).

## Accepted products and upstream base refresh

`.github/base-refresh-policy.json` pins both accepted product profiles to
`2559fa69aaf8918b5e729cfde08ca5f5c3ba4fcc`. Legacy publishes `:stable` and
compatibility `:latest` with `IMAGE_TAG=latest`; reviewed r16.1 system publishes
only `:snitchwatch-system`. The policy transition follows successful protected
promotion and independent destination signature/SPDX checks. Host runtime
acceptance remains unverified under the owner-authorized CI-based release path.

`base-refresh.yml` resolves upstream `bazzite-nvidia-open:stable` once to an
immutable digest and supplies `BASE_IMAGE` to that accepted checkout. Independent
validation checks the actual image's Bazzite version and RPM kernel against
floors `44.20261023` / `7.2.9-ogc3.1.fc44`, as well as source/profile/SPDX
identity. Build, smoke/runtime-systemd, signature, SPDX, build provenance, GitHub
attestation and host signature policy gates precede publication; a current-main
policy hash recheck rejects stale policy before public pointers move. Daily and
main-only dispatch refreshes need no per-base owner click. New features still
need protected fixed-digest acceptance and a subsequent reviewed policy update.

The exact system release baseline is
`sha256:3f9a0d238e69917a33a23710a53963e70b64526cc8e44f9d24787944dcca1f27`.
`ci/base-refresh.py` verifies its repository-key signature before the exception;
old/arbitrary digests need destination-bound signed refresh provenance, and a
missing system tag always fails closed. The legacy run2 recovery is unchanged.

The candidate `Containerfile` default pin is Bazzite `44.20261023`, digest
`sha256:d0e5e105ebf43c7ddbe3c4a5dfecac1628b0e0dcf152bbd2aee0eda3e4b4cdd4`.
Weekly `base-bump.yml` PRs maintain this default; automatic refresh independently
resolves its base. Neither path changes hardware tuning or proves host eBPF
compatibility. Both profiles retain allow/proc defaults.

## Containerfile stages

1. `ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:…` (candidate default; PR safe-pin verification leaves it unset, `latest-kernel` and accepted-product base refresh override it)
2. `FROM scratch AS ctx` + `COPY build_files /` — scripts reachable via bind mount, not baked in
3. `FROM ${BASE_IMAGE}` + OCI labels
4. `COPY system_files/ /` — static content, copied **before** build.sh so it can enable those units
5. `RUN --mount=bind,from=ctx … /ctx/build.sh` — modifications (caches: /var/cache, /var/log; tmpfs /tmp)
6. `RUN bootc container lint`

Before the final image: `snitchwatch-legacy` (`FROM scratch`, marker only) or the
opt-in system bridge, selected by `FROM snitchwatch-${SNITCHWATCH_BRIDGE} AS snitchwatch-selected`
(the final image copies only its `/out/`). The system bridge is split so that the
bridge's third-party build scripts cannot reach anything trusted later (H1):

| Stage | FROM | Network | Runs | Hands over |
|---|---|---|---|---|
| `snitchwatch-prep` | pinned `fedora:44@sha256` (= `builderImage`) | yes | `snitchwatch-system-prep.sh`: install-deps, Koji toolchain, `--record-toolchain`, dbus-daemon, online probe (positive control), clone + `sourceFiles` hash check, `bridge_artifact.py version`, `cargo fetch --locked --target x86_64-unknown-linux-gnu`, `rpm -qa` inventory | `/work/native` (to its descendants) |
| `snitchwatch-scanner` | prep | `--network=none` | offline probe, inventory `cmp`, `snitchwatch-system-scanner-build.py` | `/handoff/scanner`: 2 binaries + 10 evidence files, flat |
| `snitchwatch-bridge` | prep | `--network=none` | `snitchwatch-system-bridge-build.sh`: offline probe, fresh `CARGO_HOME` from prep's cache (only `.crate` files matching the `Cargo.lock` sha256, plus the index; no config/bin/src), exported `CARGO_NET_OFFLINE=true`, offline `cargo fetch` preflight, `build-bridge.sh` (~660 crates, root), `repro-check.sh`, executing `verify --check-ldd --run-flags`, inventory `cmp` | `/handoff/bridge`: tarball, `.sha256` sidecar, `build.log`, `repro.log` |
| `snitchwatch-bridge-rebuild` | prep | `--network=none` | `snitchwatch-system-bridge-rebuild.sh` (M1 step 2): the same lines as the bridge stage from the probe to the one-tarball check (a test compares them), at the same `/work/native` paths, in a separate container; no repro-check, no `verify`, never runs the binary; inventory `cmp` | `/handoff/bridge-rebuild`: the tarball only |
| `snitchwatch-daemon` | prep | yes | `snitchwatch-system-daemon-build.sh` (unchanged) into `/work/daemon-out`, then `handoff.py check daemon` (an extra file fails here) and `export daemon` | `/handoff/daemon`: the daemon verifier's `REQUIRED` + manifest |
| `snitchwatch-system` | prep | `--network=none` | `/tools/snitchwatch-system-assemble.sh` (tools re-copied from scratch `ctx`): offline probe, inventory `cmp`, prep checkout re-hashed, `/handoff` root-owned and not g/o-writable, `handoff.py check` ×4, daemon verify, `handoff.py compare-bridge` (the two bridge tarballs must be byte-identical), non-executing `bridge_artifact.py verify --extract-to` against the sibling's sha256, `stage.py` (bridge `--version` and upstream `stage.sh`/`--help` as uid 65534 with `no_new_privs` (fails closed), empty env, `stage.sh` timeout 300 s; root adopts only the 9 `stage.sh` paths), daemon handoff re-checked, `merge` (refuses overlap), daemon + system verifiers | `/out` |

Trust argument: the FROM chain of scanner, daemon and assembly is prep only; both bridge stages are the base
of nothing, neither reads the other, and each is read only through its allowlist. `snitchwatch-system-handoff.py check` refuses extra,
missing, symlinked, special, hard-linked, set-id, capability/ACL-carrying, wrongly owned or wrongly moded
entries (an xattr listing error other than "not supported" is a refusal); assembly never runs code from a handoff and never sees the scanner's source export.
`pins.json`, the staged file set, modes and provenance formats (bytes included) are unchanged, so `EXPECTED_PINS_SHA256`
and base-refresh `validate-system` do not move. Static contracts: `tests/test-snitchwatch-image-stages.py`.
Only a CI build proves: buildah 1.33.7 accepts and enforces `RUN --network=none` (the in-build probe
fails loudly if not), unused-stage skipping still keeps `fedora:44` out of the legacy legs, 65534
can be entered in the build user namespace, and disk fits the extra stage commits.
M1 step 1 (bridge offline): the bridge build scripts cannot reach the network (no second-stage download,
no exfiltration over the network; the tarball and the public CI log still leave the stage). The build uses
only pinned inputs plus the live Fedora RPMs; byte-reproducibility is not verified by this step. Only CI proves that the bridge
build (including `build-bridge.sh`'s own license `cargo fetch` and `repro-check.sh`'s nested rebuild)
succeeds offline from prep's cache; the preflight fails first, with its own message, if the cache is short.
The hand-over is allowlisted, not read-only: read-only means nothing against root in the stage (step 3).
M1 step 2 (sibling rebuild): assembly runs `handoff.py compare-bridge` before reading either tarball and
refuses any byte difference, printing both sha256s, the first differing offset and the differing tar
members (parsed in memory, diagnosis only). `stage.py` derives `reproducible` from its own comparison
(refusing a mismatch or the same file twice) instead of a literal, and `verify --expect-sha256` takes the
sibling's digest. The staged provenance is byte-identical when the builds agree (`reproducible` is still
`true`, `reproLogSha256`/`buildLogSha256` still hash the first stage's logs, `reproCommand` is unchanged),
so `verify.py`, `EXPECTED_PINS_SHA256` and `validate-system` do not move. Basis: the tarball is canonical
(fixed mtime/owner/order, gzip mtime 0, `--remap-path-prefix`), and CI runs on different runners gave the
same sha256 (`eb2120…`, source 5860dcdb) online and offline. Cost: one more bridge build, ~3 min of the
~17.5 min system leg. A tampered toolchain/packer/registry or a time-, random-, uid- or host-conditional
build script in one stage alone is now caught; a non-deterministic build fails the leg closed.
Still open (M1): a deterministic hostile crate already in `Cargo.lock` builds the same bytes in both stages
(only lock-bump review covers it; this does not make the lock trustworthy); identical poisoning of both
stages (same runner, same buildah, no host isolation); `build.log`/`repro.log` and the in-stage
`repro-check.sh` are still produced by the first stage; both builds run as root. Next: an unprivileged
read-only build (step 3).

## build.d scripts (executed in filename order)

`build.sh` is a thin runner: it `bash`-executes each `build_files/build.d/*.sh` in
glob order. Numbers encode execution order, which is load-bearing — `40-` must run
after `30-` and before `60-`. Filenames replace the line ranges this table used to
carry, because line ranges drift and filenames do not.

| Script | Effect |
|---|---|
| `05-pin-kde-packages.sh` | `/etc/dnf/dnf.conf` `exclude=` for the KDE Plasma/KWin package family, so this build's own dnf transactions can't skew `kwin` ahead of `kscreenlocker` (see `docs/research/kwin-screenlocker-abi-2026-08-08/`). Must run before any other script's `dnf install` |
| `10-virt-packages.sh` | dnf: qemu-kvm, libvirt*, virt-install/manager/viewer, edk2-ovmf, guestfs-tools, spice-gtk3 |
| `15-signature-policy.sh` | migrates the global policy default to `reject`, adds a Docker-only empty-scope compatibility fallback if absent, merges the repository-scoped cosign `sigstoreSigned` rule, and installs sigstore attachments without erasing unrelated explicit policy rules |
| `20-dev-tooling.sh` | dnf: android-tools, ccache, flatpak-builder, podman-machine/tui, rclone, restic, zsh |
| `30-docker-ce.sh` | write inert `docker-ce.repo` (every section enabled=0); remove `podman-docker`; install via `--enablerepo=docker-ce-stable`, including explicit `iptables` for the Docker/libvirt forwarding helper |
| `40-sysusers-fixup.sh` | **generic** orphan strip (keep only shadow/gshadow lines with a matching passwd/group) -> `systemd-sysusers` -> guarded `groupadd -r qemu` + `useradd qemu` + **`groupadd -r docker`**. Fixes virtqemud + docker.socket "Unknown group" boot failures. The most fragile piece; kept isolated on purpose |
| `50-docker-networking.sh` | intentional no-op: `ujust enable-docker` creates the Docker-only `iptable_nat` modules-load file locally |
| `60-libvirt-services.sh` | mask `libvirtd.service`; enable `virtqemud/virtnetworkd/virtnodedevd/virtnwfilterd/virtstoraged/virtproxyd.socket`; explicitly disable Docker service/socket; default NAT net autostart symlink (virsh can't run at build time); polkit `wheel` -> `qemu:///system`; enable `bazzite-tower-firstboot.service` (kvm/libvirt only); enable `docker-libvirt-forwarding.path` / `.timer` |
| `64-endpoint-events-packages.sh` | dnf: rsyslog, rsyslog-mmjsonparse, rsyslog-gnutls for the opt-in Vigie endpoint-event feed; explicitly `systemctl disable` every unit those RPMs ship (no mask, no `|| true`), fails if `rsyslog.service` is absent or any unit is still enabled. Tested with PATH shims in `tests/test-endpoint-events-packages.py` |
| `70-guards-monitoring.sh` | Wi-Fi guard + SMART, opt-in Cockpit; install bpftool/sysstat/igt-gpu-tools/trace-cmd, explicitly disable sysstat.service + collect/summary/rotate timers and trace-cmd.service, retain sysstat.sleep bytes but force0644 to prevent unconditional suspend collection (intentional RPM mode override) |
| `80-ras-microcode.sh` | dnf rasdaemon (enable); **mask `mcelog.service`**; dnf microcode_ctl (latest) |
| `90-power-thermal.sh` | dnf thermald (enable); enable `bazzite-tower-power-tuning.service` (balanced EPP + platform-profile). SOF audio: **no install** — bypassed via the `dsp_driver=1` karg |
| `95-firewall.sh` | Firewall selector. Default `opensnitch`: pinned v1.8.0 RPM extraction, Snitchwatch config + enablement. `SNITCHWATCH_BRIDGE=system` opts into the pinned native bridge/system overlay; legacy remains default. `FIREWALL_DAEMON=portmaster` is a disabled VM spike, sourcing `../firewall/portmaster.sh`: source-build of the exact Portmaster v2.2.1 commit, direct core (no updater/bootstrapper), config pinned from `/usr` via `BindReadOnlyPaths=`, Go toolchain removed after build, OpenSnitch masked. Build with `just build-portmaster-spike`; never a default image |
| `97-vm-gate-ssh.sh` | `VM_GATE_SSH=1` (off by default, `:latest` unaffected): enables `sshd.socket` so VM-gate testing (`just run-vm-ssh`) can SSH in instead of needing a GUI console. `build-portmaster-spike` already passes this build-arg |
| `98-rebrand-motd.sh` | overwrites the identity fields (`image-name`/`image-vendor`/`image-ref`/`image-tag`/`image-branch`) in `/usr/share/ublue-os/image-info.json` from the upstream base's own identity to `bazzite-tower`'s — the login MOTD template reads this file directly, so left unmodified it always claims the machine is running the upstream base image. `image-tag`/`image-branch` come from the `IMAGE_TAG` build-arg (Containerfile `ARG IMAGE_TAG=latest`, CI passes `latest`/`latest-kernel`/`snitchwatch-system` per matrix leg — a `:latest-kernel` machine's MOTD would otherwise wrongly claim `:latest`) |
| `99-cleanup.sh` | `dnf clean all` |

`FIREWALL_DAEMON`, `SNITCHWATCH_BRIDGE` and `VM_GATE_SSH` reach `95-firewall.sh`/`97-vm-gate-ssh.sh`
as inherited environment variables — the Containerfile sets them as a
command-prefix on the `RUN`, so the runner's shell has them and every child
`bash` inherits them.

## Snitchwatch image-consumer gate

`SNITCHWATCH_BRIDGE=legacy` is the default and does not compile a native bridge.
The opt-in `system` profile requires `FIREWALL_DAEMON=opensnitch` and consumes a
native version 0.1.1 release built from an exact clean Git source/submodule checkout using the
pinned Fedora 44 builder. Source, RPM toolchain inventory, licenses and binary
identity accompany the original release artifact. The installer extracts only
verified binary/licenses and stages system units from that same source; its
separate installed-overlay manifest hashes immutable files and records their
identity. No legacy release-install/global-user-enable script runs.

The system candidate also builds the pinned OpenSnitch 1.8.0 source with its
reviewed downstream repair. Separate daemon provenance binds the patch, module
locks, generated protocol files, Go/protoc toolchain, RPM inventory, licenses
and actual `/usr/bin/opensnitchd` hash. Both manifests are checked by smoke
and boot tests; the boot check verifies the running daemon executable when
NFQUEUE is available. Rootless container checks cannot replace the VM gate.
Legacy builds retain upstream RPM extraction and incur no native compilation.
Both profiles retain the RPM-supplied `/usr/lib/opensnitchd/ebpf/opensnitch.o`,
`opensnitch-procs.o` and `opensnitch-dns.o`: the native Go executable does not
embed them. `95-firewall.sh` fails closed through
`bazzite-tower-opensnitch-ebpf-check` when any object is missing, nonregular or
has an invalid ELF64 relocatable BPF header; smoke repeats the packaging check.
This is not proof that probes load on the running kernel; the proc baseline
and readiness contract remain unchanged. `tests/test-snitchwatch-resources.py`
exercises invalid/missing object fixtures and interval resource accounting.

Only the system candidate changes the image-intent OpenSnitch address to
`unix:opensnitchd.sock`; both profiles permanently retain `DefaultAction: allow`
and `ProcMonitorMethod: proc`. Deny enforcement requires explicit operator opt-in. Upstream `stage.sh` installs the
`opensnitch.service.d/snitchwatch-system-bridge.conf` drop-in (`Wants=`/`After=`
the gRPC socket, `WorkingDirectory=-/run/snitchwatch`), pinned by hash; it also
stages upstream's `ebpf` daemon config, which `snitchwatch-system-stage.py`
removes from the overlay (unit-tested); `95-firewall.sh` installs the tower's `allow`/`proc`
candidate after the RPM extraction. `snitchwatch-system-stage.py` also stages the
tower's always-on OS-update rules (`snitchwatch-system-os-update-rule.json` ->
`/etc/opensnitchd/rules/000-snitchwatch-tower-os-update.json`, `snitchwatch-system-resolved-dns-rule.json`
-> `000-snitchwatch-tower-resolved-dns.json`; digests pinned in the verifier). Sysusers are materialized
before tmpfiles and socket startup. Existing live bootc `/etc` drift is a
migration decision, not an implicit policy overwrite. Migration retains allow;
system readiness rejects an explicit local deny override as policy drift.

r16.2 preparation keeps the bridge pin at Snitchwatch 5860dcd (AI cache export and
the upstream Deep Scan host scanner). `snitchwatch-system-scanner-build.py` archives the
verified source into a separate build copy (no tower patch is applied),
verifies every file of the two built crates (18: `scanner-privileged` 15, `scanner-broker` 3) against the pinned `sourceFiles`
(unpinned, missing or changed files are refused; the verifier requires the pinned set to equal its allowlist), and
performs two locked offline release builds with separate target directories.
It requires byte-identical scanner/broker binaries, resolved ELF dependencies,
Rust tests plus fake-authorization private-D-Bus tests, and records source input,
Cargo, actual Rust/RPM toolchain, normal/build dependency closure, license text,
test/build/repro log and executable hashes. Isolation is the stage split above (H1 closed for the
scanner, the daemon and assembly). Scanner-build input hardening on top of it (defence in depth):
the scanner build uses a fresh `CARGO_HOME` (`out/cargo-home`) rebuilt only from prep's
cached `.crate` files after checking each sha256 against the pin-checked export `Cargo.lock`
(extracted `registry/src`, config and `bin` are not carried over; cargo re-extracts); refuses any
`.cargo/config[.toml]` in the export, every ancestor to `/` and the fresh `CARGO_HOME`; and compares a
partial toolchain fingerprint (only the files of the `rust`, `cargo` and `rust-std-static` RPMs, recorded by
`--record-toolchain` after install in `snitchwatch-system-prep.sh`) before building.
The child environment is an allowlist; build-steering variables (`CC*`, `*FLAGS`, `LD_*`, `RUST*`,
`CARGO_*` beyond home/target/jobs, `PYTHON*`) are refused. `bridge_artifact.py` is pin-checked and, with
`LICENSE`, taken from the export inside the scanner stage; assembly uses the prep checkout's copy after
re-hashing it. Before the split, the bridge build scripts ran as root with network in the same `RUN`
and could tamper with the checker, the fingerprint, PATH tools or `/etc/ld.so.preload`; now they run
only in `snitchwatch-bridge` and its sibling `snitchwatch-bridge-rebuild`, which no other stage descends from and which have no network.
The pristine bridge release/repro
path and unchanged upstream dependency-list cmp remain independent.

`snitchwatch-system-stage.py` invokes the pinned upstream scanner stage script
for exactly two0755 executables, one session-bus activation file, the unchanged
auth_admin polkit action and root0700 tmpfiles definition, then installs
scanner license/provenance receipts under `/usr/share`. The system verifier's
exact 42-file inventory binds those inputs, patch/evidence and both ELF hashes;
it refuses persistent scanner systemd service/timer/socket activation. Four more
files are the inert opt-in endpoint-events set (pinned from `packaging/endpoint-events/`,
staged under `/usr/share/snitchwatch/endpoint-events/`); the verifier refuses any enabled
link or unit copy of them, any daemon `Loggers` key and any `opensnitchd-logger*` file. The patch
hardens fixed external primitives (bounded/status-checked output, trusted PATH,
stable locale, process-group cleanup coordinated with the540s root watchdog)
and refuses unsafe existing private state/database/sidecars. It does not run
scans or repair host state. Smoke adds runtime linker/pkexec and legacy-exclusion
assertions; the boot gate requires tmpfiles-provisioned root:root0700 state.
`tests/test-snitchwatch-image-build.py` covers negative inventory, evidence,
source-patch and environment-override cases. Actual root authorization, SELinux,
AI export and owner scan behavior require later candidate runtime acceptance;
upstream file-drift checking remains skipped. Base-refresh policy is unchanged;
its tests model accepted source separately from newer control/product pins.
The offline snapshot gate now includes the installed-branch metadata fixture
used by the system authority tests; fixture absence cannot masquerade as a
working readiness check.

Daemon toolchain (golang, golang-bin, golang-src, protobuf, protobuf-compiler,
libnetfilter_queue, libnetfilter_queue-devel) is not installed from live repos:
`snitchwatch-system-toolchain.sh` downloads the exact Koji RPMs pinned in
`snitchwatch-system-daemon-pins.json` (`toolchainRpms`, signed copies only),
checks size, sha256, the Fedora signature (`rpm -K` must say `signatures`) and
NEVRA, then installs them in one dnf transaction. The version-string gate in
`snitchwatch-system-daemon-build.sh` remains. Koji is a build-time dependency
(fails closed). A `fetch` mode does the same verification without installing; `tests/test-snitchwatch-daemon-patch.sh` uses it in its network phase so the offline test phase installs the same pinned RPMs. Test: `tests/test-snitchwatch-toolchain.sh`.

Phase 2 adds three signed Koji RPMs (rust, cargo, rust-std-static
1.99.0-1.fc44) to `snitchwatch-system-pins.json` for the bridge. The same helper
runs after the byte-identical upstream installer and before the Rust version
gate in `snitchwatch-system-prep.sh`; the daemon retains its seven pins.
Remaining live toolchain and transitive dependencies, including LLVM, are not
fully hermetic. Re-pinning either manifest requires updating
`EXPECTED_PINS_SHA256` in its corresponding bridge `snitchwatch-system-verify.py`
or daemon `snitchwatch-system-daemon-verify.py` and rerunning relevant tests.

`just build-snitchwatch-system` builds the local candidate.
`just build-snitchwatch-system-vm` additionally opts into `VM_GATE_SSH=1` for a
disposable VM. Fresh build/first-boot/SELinux evidence remains distinct from
the [historical bridge tests](../research/snitchwatch-system-bridge.md).


## Verified by

`tests/smoke.sh` (offline, post-build) asserts each of the above survived the build;
`tests/boot-check.sh` proves the QEMU path works + no SOF storm at runtime. See [ci-cd.md](ci-cd.md).

## Migration journal refusal coverage

`tests/test-snitchwatch-system.py` checks rollback refusal for malformed or
truncated `current.json`, unknown schema/status and missing recorded config
bytes before any service command or policy publication. It also checks repeated
rollback preserves the restored baseline and repeated apply refuses an active
`applied`/`prepared`/`recovery-required` journal. Assertions preserve exact policy
bytes/hash/security metadata, service/socket state and journal bytes/modes, and
exclude unit mutations and journal writes. JSON decode failures are ValueError
and missing required fields are KeyError; the migration CLI catches both and
reports REFUSED. These are offline fixture guarantees, not host runtime evidence.

## Offline Snitchwatch gate

`just snitchwatch-gate` wraps the existing toolchain, image, daemon image, system
fixture and supply-chain tests. `scripts/snitchwatch-gate.py` snapshots the exact
input bytes/modes into a unique `/tmp/snitchwatch-gate.*` evidence directory
(or a new `--evidence PATH`). `inputs.json` records SHA-256 hashes, including
separate hashes of two scratch copies whose recursive shell cleanup is replaced
with bounded Python cleanup. Original files and assertions remain unchanged.
Each check runs in a fresh network namespace; unavailable isolation/tools or
a 120-second timeout is BLOCKED. No package fetch, container/image/VM build or
real daemon is invoked. `summary.json` records per-check PASS/FAIL/BLOCKED,
commands, exit codes, log hashes and concise first diagnostics; complete logs
and executed inputs remain alongside it. Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED.
Offline PASS never certifies runtime: runtime is always UNVERIFIED (GUI,
NFQUEUE, SELinux and deployment require separate owner-controlled evidence).

## r13 publication boundary

The r15.1 bridge/GUI candidate is `5c969a2e21dc7f5c976e7048aa9f95e5663c7de2`,
tree `a6f415313f30e81b6bca9bb839aadd0f2f908af3`, with unchanged OpenSnitch
gitlink `b404c4c6316760fa7bc415509d3f8d747f7dc9cc` (r13/r14: `bf15bb5`). The
bridge pins record this identity and hashes of all 15 committed packaging inputs; the installed
verifier authenticates the refreshed pins hash. Upstream installer bytes and
signed Koji/toolchain/daemon pins remain unchanged. All 15 packaging-input
digests, Flatpak finish arguments, native bridge and wire source remain unchanged
from r15; this repin selects the GUI learning/naming work. This candidate pin does not
establish independent review or runtime acceptance; authority remains held.

The system profile remains PR-tested; `build.yml` does not automatically publish it. Accepted-product base refresh uses the separate policy-pinned path described above. `r13-candidate.yml` builds/tests a reviewed source in a credential-free job, records actual installed binary/manifest/pins identities through `ci/r13-candidate-data.py`, and exports digest-preserving OCI data. The collector supplies `/usr/bin/true` when creating its never-started extraction container because bootc images may omit CMD/ENTRYPOINT; its separate RPM inventory run remains unchanged. Extraction containers are removed on success and failure. Full collector tests in `tests/test-r13-publication.py` cover commandless images, installed-binary/archive drift and cleanup, preserving the actual artifact validator. Separate checkout-free publishers and `ci/r13-publication.py` trusted control gates enforce fixed targets for the candidate. The publisher keeps legacy keyed signing for repository policy, adds a same-key Cosign bundle and attests/verifies SPDX and the original build predicate with `--new-bundle-format` (legacy `verify-attestation` trips on the certificate-based GitHub provenance on the same digest); its policy-gated local OCI export uses `--remove-signatures` only because OCI cannot store signatures. Promotion copies the one fixed digest, re-signs it and re-attests its SPDX predicate without an image rebuild. Base-image/toolchain pins and smoke/boot assertions are preserved. See [r13-publication.md](../r13-publication.md) for the trust bootstrap, immutable identity schemas and the promoter steps.

`release.yml` is a minimal fixed-digest promoter for the image only; it
acquires no GUI bytes. `tests/test-r13-publication.py` executes the
`ci/r13-publication.py` candidate gates and the promoter's fake-tool
steps; remote CI and runtime still require independent evidence.

The candidate collector exports the current user's containers-storage image
through `podman unshare skopeo copy --all --preserve-digests`. This uses the
existing Podman user namespace and image store, without sudo or runner security
changes. The full collector regression models direct Skopeo unshare refusal and
checks namespace-export failure cleanup. Actual runner success requires fresh CI.
See [Podman unshare](https://docs.podman.io/en/latest/markdown/podman-unshare.1.html).

## Diagnostic package defaults (r16.1 source preparation)

The four diagnostic packages use Fedora repositories. Source availability is
verified; actual new-image installation/operation is not yet built or accepted.
The early `00-bazzite-tower-diagnostics.preset` supplies disabled fresh-image
defaults before RPM scriptlets; explicit disables after install enforce the
image state. No masks, running-host changes or tool invocations are added.
`sysstat.sleep` must exist as a regular non-symlink file before its executable
mode is removed; timers remain independently opt-in. `tests/smoke.sh` adds package
and executable presence, all five disabled units and nonexecutable retained sleep
hook assertions. These are built-image gates, unrun until an image build.
