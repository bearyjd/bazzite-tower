<!-- Generated: 2026-11-26 | Files scanned: 3 | Token estimate: ~850 -->
<!-- Targeted update: 2026-10-05 | Snitchwatch image-consumer gate; not a full regeneration -->
# Image Build Pipeline

`Containerfile` → `build_files/build.sh` (runs inside the build, `set -euo pipefail`).

## Accepted products and upstream base refresh

`.github/base-refresh-policy.json` pins both accepted product profiles to
`fe35a4f39574f1610c5f5d5842cf9029945a3e6c`. Legacy publishes `:stable` and
compatibility `:latest` with `IMAGE_TAG=latest`; reviewed r16.1 system publishes
only `:snitchwatch-system`. Historically, the r16.1 policy transition followed protected
promotion and independent destination signature/SPDX checks. Host runtime
acceptance was not established under the owner-authorized CI-based release path.

`base-refresh.yml` resolves upstream `bazzite-nvidia-open:stable` once to an
immutable digest and supplies `BASE_IMAGE` to that accepted checkout. Independent
validation checks the actual image's Bazzite version and RPM kernel against
floors `45.20261105` / `7.2.15-ogc1.1.fc45`, as well as source/profile/SPDX
identity. Build, smoke/runtime-systemd, signature, SPDX, build provenance, GitHub
attestation and host signature policy gates precede publication; a current-main
policy hash recheck rejects stale policy before public pointers move. Daily and
main-only dispatch refreshes need no per-base owner click. New features need a
merged pin PR and a reviewed policy update; owner laptop testing is not
required first (owner decision 2026-11-27).

The exact system release baseline is
`sha256:3f9a0d238e69917a33a23710a53963e70b64526cc8e44f9d24787944dcca1f27`.
`ci/base-refresh.py` verifies its repository-key signature before the exception;
old/arbitrary digests need destination-bound signed refresh provenance, and a
missing system tag always fails closed. The legacy run2 recovery is unchanged.

The candidate `Containerfile` default pin is the digest in `ARG BASE_IMAGE` (read it
there; this map deliberately does not copy it, it changes with every bump).
Weekly `base-bump.yml` PRs maintain this default; automatic refresh independently
resolves its base. Neither path changes hardware tuning or proves host eBPF
compatibility. Both profiles default to allow + ebpf (pinned `Ebpf.ModulesPath`); `proc` is the documented one-key fallback. Not shipping before owner gates A (VM load test) and C (laptop day-run).

## Containerfile stages

1. `ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:…` (candidate default; PR safe-pin verification leaves it unset, `latest-kernel` and accepted-product base refresh override it)
2. `FROM scratch AS ctx` + `COPY build_files /` — scripts reachable via bind mount, not baked in
3. `FROM ${BASE_IMAGE}` + OCI labels
4. `COPY system_files/ /` — static content, copied **before** build.sh so it can enable those units
5. `RUN --mount=bind,from=ctx … /ctx/build.sh` — modifications (caches: /var/cache, /var/log; tmpfs /tmp)
6. `RUN bootc container lint`

Before the final image: `snitchwatch-legacy` (`FROM scratch`: marker plus the per-kernel eBPF
overlay assembled by the `ebpf-legacy-*` stages below) or the
opt-in system bridge, selected by `FROM snitchwatch-${SNITCHWATCH_BRIDGE} AS snitchwatch-selected`
(the final image copies only its `/out/`). The system bridge is split so that the
bridge's third-party build scripts cannot reach anything trusted later (H1):

| Stage | FROM | Network | Runs | Hands over |
|---|---|---|---|---|
| `snitchwatch-prep` | pinned `fedora:45@sha256` (= `builderImage`) | yes | `snitchwatch-system-prep.sh`: install-deps, Koji toolchain, `--record-toolchain`, dbus-daemon, online probe (positive control), clone + `sourceFiles` hash check, `bridge_artifact.py version`, `cargo fetch --locked --target x86_64-unknown-linux-gnu`, `rpm -qa` inventory | `/work/native` (to its descendants) |
| `snitchwatch-scanner` | prep | `--network=none` | `snitchwatch-system-scanner-stage.sh`: offline probe, inventory `cmp`; root: `scanner-build.py --phase inputs` (git archive, pin checks, allowlisted cargo home, toolchain digest) into new `scanner-inputs`/`scanner-cargo`, `unpriv.py prepare … scanner` + `selfcheck`; as uid 65534 (`unpriv.py run`): `--phase build` (both builds and their compare, `cargo test`, private-bus test, `ldd`, `cargo metadata`/`tree`, licenses, provenance); root: export | `/handoff/scanner`: 2 binaries + 10 evidence files, flat |
| `snitchwatch-bridge` | prep | `--network=none` | `snitchwatch-system-bridge-build.sh`: offline probe, fresh `CARGO_HOME` from prep's cache (only `.crate` files matching the `Cargo.lock` sha256, plus the index; no config/bin/src), exported `CARGO_NET_OFFLINE=true`, `unpriv.py prepare` + `selfcheck`, then as uid 65534 (`unpriv.py run` → `bridge-unpriv.sh`): offline `cargo fetch` preflight, `build-bridge.sh` (~660 crates), `repro-check.sh`, executing `verify --check-ldd --run-flags`; root: inventory `cmp`, export | `/handoff/bridge`: tarball, `.sha256` sidecar, `build.log`, `repro.log` |
| `snitchwatch-bridge-rebuild` | prep | `--network=none` | `snitchwatch-system-bridge-rebuild.sh` (M1 step 2): the same lines as the bridge stage from the probe to the one-tarball check (a test compares them), at the same `/work/native` paths and also as uid 65534, in a separate container; no repro-check, no `verify`, never runs the binary; inventory `cmp` | `/handoff/bridge-rebuild`: the tarball only |
| `snitchwatch-daemon` | prep | yes | `snitchwatch-system-daemon-build.sh` (unchanged) into `/work/daemon-out`, then `handoff.py check daemon` (an extra file fails here) and `export daemon` | `/handoff/daemon`: the daemon verifier's `REQUIRED` + manifest |
| `ebpf-kernel` | `${BASE_IMAGE}` | `--network=none` | `snitchwatch-system-ebpf.py kernel-export` (Track A): refuses unless `rpm -q kernel-core`, `rpm -q kernel-devel`, the one `/usr/src/kernels/<r>` and the one `/usr/lib/modules/<r>` agree and `<r>/.config` is byte-identical to `/usr/lib/modules/<r>/config` (message names both versions; missing kernel-devel fails, never a Fedora-header fallback; the container's running kernel is never read); deterministic tar of the header tree (owner 0:0, mtime 0, in-tree symlinks only) | `/handoff/ebpf-kernel`: `kernel-headers.tar` + `kernel-facts.json` |
| `snitchwatch-ebpf-prep` | prep | yes | `snitchwatch-system-ebpf-prep.sh`: online probe, `ebpf.py check-pins` (digest anchor, before any download), `toolchain.sh install snitchwatch-system-ebpf-pins.json` (clang21/clang21-libs/clang21-resource-filesystem/llvm21/llvm21-libs/llvm21-filesystem 21.1.8-10.fc45, signed Koji, size+sha256+sig+NEVRA+header digest; clang 23 cannot build these modules) + live `make`, `rpm -qa` inventory. Kept out of prep so scanner/bridge/daemon/assembly ancestry has no compiler | `/work/ebpf` (to the two compiles) |
| `snitchwatch-ebpf` / `snitchwatch-ebpf-rebuild` | ebpf-prep | `--network=none` | one script, `snitchwatch-system-ebpf-build.sh`, same fixed `/work/ebpf` paths, differing only in handoff kind: offline probe, inventory `cmp` (tool checked first: a missing `cmp` is reported as such, never as a difference; legacy prep installs `diffutils` with `make git-core`), `handoff.py check ebpf-kernel`, `ebpf.py build` (daemon/module pairing check, tools owned by the pinned RPMs, reporting 21.1.8 and clean under `rpm -V`, tarball digest vs facts, `ebpf_prog` extracted as raw git blobs from prep's verified `vendor/opensnitch` checkout and tree-sha256 checked, `make -B -R -r` with explicit `KERNEL_VER`/`KERNEL_DIR`/`KERNEL_HEADERS`/`KERNEL_ARCH`, `CC="clang-21 -DCC_USING_FENTRY -fms-extensions -Wno-microsoft-anon-tag"`, `LLC=llc-21`, `llvm-strip-21 -g`, mode 0644); both are ancestors of nothing; the compile runs as root (accepted deviation SEC L1: no uid-65534 drop; follow-up: `setpriv` uid 65534 + chown 0:0 at export) | `/handoff/ebpf`: 3 `.o`, `kernel-facts.json`, `build-info.json`, `build.log`; `/handoff/ebpf-rebuild`: the same minus the log |
| `ebpf-legacy-base` | pinned `fedora:45@sha256` | yes | `snitchwatch-system-ebpf-legacy-base.sh`: `dnf install python3` (absent from the bare image) |  |
| `ebpf-legacy-prep` | legacy-base | yes | the same `snitchwatch-system-ebpf-prep.sh` with `SNITCHWATCH_BRIDGE=legacy`: `check-pins`, Koji clang21/llvm21 + `make git-core`, then `git fetch --depth=1` of the one pinned OpenSnitch commit b404c4c6 (v1.8.0, no `--filter`: the offline compile reads blobs with `cat-file`) into `/work/ebpf/source`; `rpm -qa` inventory | `/work/ebpf` |
| `ebpf-legacy` / `ebpf-legacy-rebuild` | legacy-prep | `--network=none` | the same `snitchwatch-system-ebpf-build.sh` (`legacy`: `--repo /work/ebpf/source`, no daemon/native pins), same fixed paths; `ebpf_prog` tree sha256 re-verified offline | `/handoff/ebpf` / `/handoff/ebpf-rebuild` (same kinds as system) |
| `ebpf-legacy-assembly` | legacy-base (no compiler in its ancestry) | `--network=none` | `snitchwatch-system-ebpf-legacy-assemble.sh`: probe, handoff checks, byte-identical `compare-ebpf`, `ebpf.py stage --profile legacy`, profile marker | `/out`, copied by `snitchwatch-legacy` |
| `snitchwatch-system` | prep | `--network=none` | `/tools/snitchwatch-system-assemble.sh` (tools re-copied from scratch `ctx`): offline probe, inventory `cmp`, prep checkout re-hashed, `/handoff` root-owned and not g/o-writable, `handoff.py check` ×4, daemon verify, `handoff.py compare-bridge` (the two bridge tarballs must be byte-identical), `handoff.py check ebpf`/`ebpf-rebuild` + `compare-ebpf` (objects, facts and build info byte-identical), non-executing `bridge_artifact.py verify --extract-to` against the sibling's sha256, `stage.py` (bridge `--version` and upstream `stage.sh`/`--help` as uid 65534 with `no_new_privs` (fails closed), empty env, `stage.sh` timeout 300 s; root adopts only the 9 `stage.sh` paths), daemon handoff re-checked, `merge` (refuses overlap), `check ebpf-kernel-facts` + `ebpf.py stage` (refuses unless ebpf-kernel's own facts, COPYed directly, equal the compile handoff's; manifest from the pins, facts and hashes taken here) + `merge` of the eBPF overlay, daemon + system verifiers | `/out` |

Trust argument: the FROM chain of scanner, daemon and assembly is prep only; both bridge stages are the base
of nothing, neither reads the other, and each is read only through its allowlist. `snitchwatch-system-handoff.py check` refuses extra,
missing, symlinked, special, hard-linked, set-id, capability/ACL-carrying, wrongly owned or wrongly moded
entries (an xattr listing error other than "not supported" is a refusal); assembly never runs code from a handoff and never sees the scanner's source export.
`pins.json`, the staged file set and modes are unchanged (provenance gained one key in M1 step 3, below), so `EXPECTED_PINS_SHA256`
and base-refresh `validate-system` do not move. Static contracts: `tests/test-snitchwatch-image-stages.py`.
Only a CI build proves: buildah 1.33.7 accepts and enforces `RUN --network=none` (the in-build probe
fails loudly if not), unused-stage skipping still keeps `fedora:45` out of the legacy legs, 65534
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
sibling's digest. `reproducible` is still `true` when the builds agree; `reproLogSha256`/`buildLogSha256`
still hash the first stage's logs. Since M1 step 3 `reproCommand` names `compare-bridge` and
`reproducibleBasis` says what `reproducible` rests on; `verify.py` and `validate-system` read the provenance
with `.get` only and its bytes already differ per build (cargo's elapsed time in `build.log`), so no pin moves. Basis: the tarball is canonical
(fixed mtime/owner/order, gzip mtime 0, `--remap-path-prefix`), and CI runs on different runners gave the
same sha256 (`eb2120…`, source 5860dcdb) online and offline. Cost: one more bridge build, ~3 min of the
~17.5 min system leg. A build script conditional on anything that differs between the two stages (process
tree, time, randomness, tampering done in one stage only) is caught; one conditional on the uid, user or
hostname is not (the same in both). A non-deterministic build fails the leg closed.
Still open (M1): a deterministic hostile crate already in `Cargo.lock` builds the same bytes in both stages
(only lock-bump review covers it; this does not make the lock trustworthy); identical poisoning of both
stages (same runner, same buildah, no host isolation); `build.log`/`repro.log` and the in-stage
`repro-check.sh` are still produced by the first stage.
M1 step 3 (unprivileged build, both stages): root runs the probe, the allowlisted cargo home, the rpm `cmp`,
the logs (`tee` into root-owned `$work`) and the export. `snitchwatch-system-unpriv.py prepare` refuses unless
`/work`, `$work` and the source checkout (`.git` included) are root-owned with no group/other write and
other-readable and nothing on the image filesystem is owned by 65534 (other mounts such as `/proc` show an
unmapped owner as 65534 and get the selfcheck's access test instead), then hands 65534 only `bridge-cargo` and
new empty `dist`, `target`, `home`. `run` forks; the child drops the bounding, ambient and inheritable caps,
all groups, gid and uid, sets `no_new_privs` and refuses to exec unless `/proc/self/status` shows exactly
that (fails closed; no `setpriv`, since adding a package to prep would change the `rpm -qa` set embedded
in the tarball); afterwards it SIGKILLs leftover descendants (subreaper). `selfcheck`, as 65534, prints one
JSON line (uid, caps, NoNewPrivs, PATH, HOME), proves writes to `/usr/bin`, `/usr/local/bin`,
`/etc/ld.so.preload`, `/usr/lib/sysimage/rpm`, `/factory`, `$work`, the source, prep's cargo cache, the
toolchain and `cc`/`gcc`/`ld`/`rustc`/`cargo`/`python3` fail, the scratch dirs are writable, and walks the
filesystem: anything else writable by or owned by 65534 is a refusal (sticky dirs excepted). Third-party
steps run only via `snitchwatch-system-bridge-unpriv.sh` (refuses root), named only by the two stage
scripts. Paths, exported env and umask 022 are unchanged; PATH is the fixed container default and HOME is
`$work/home` (repro-check already proves HOME independence). The source stays 0755/0644 rather than
0555/0444: `repro-check.sh` tars the tree to `/tmp` and its EXIT-trap `rm -rf` cannot delete a copied
0555 tree as non-root, so read-only is by ownership. Only CI proves the drop, the walk and byte equality
(`eb2120…`) under 65534. Still open (M1): the deterministic hostile crate in `Cargo.lock` (both stages
build it alike); within a stage the build uid can still edit other crates' unpacked sources under
`bridge-cargo/registry/src` (cargo must write its home) and its own outputs; nothing checks that the two
stages run on independent hosts; kernel escape.
M1 step 3 for the scanner stage (same helper, profile `scanner`): `scanner-build.py` is split. `--phase inputs`
runs as root and runs no cargo, rustc, build script or built file: git archive into `$work/scanner-inputs/source`,
the pin checks, `source-inputs.json`, `build-inputs.json` (SOURCE_DATE_EPOCH from `git log`, which 65534 could not
run on the root-owned checkout), the allowlisted `$work/scanner-cargo`, the cargo-config and toolchain-digest checks.
`prepare "$work" scanner` additionally requires `scanner-inputs` root-owned read-only and hands 65534 only
`scanner-cargo` and new empty `scanner` (the output, unchanged `export scanner "$work/scanner"`) and `home`; its
selfcheck also probes writes to `scanner-inputs`, the export, its `Cargo.lock`, both json records and
`/factory/snitchwatch-system-scanner-build.py`. `--phase build` refuses euid 0 first and runs everything else
as 65534. After it root overwrites the build uid's `source-inputs.json` with its own copy from `scanner-inputs`, exports the allowlist
(each file opened `O_NOFOLLOW|O_NONBLOCK`, fstat'd regular with one link, read from that fd; all kinds) and removes the three dirs.
The scanner selfcheck also requires `rpm-inventory.txt` and the pins file readable by 65534. The export path moved
(`out/source` → `scanner-inputs/source`); `--remap-path-prefix` maps it to the same `/snitchwatch`, and no pin
or verifier holds a scanner binary hash (assembly checks them against the stage's own provenance). Consequence:
the scanner's two-build compare, test logs and provenance are now produced by 65534 and can be forged by a
hostile crate, as the bridge's repro-check can. Only CI proves the scanner drop and selfcheck, that `cargo test`
and the private-bus test (dbus-daemon) pass as a uid without a passwd entry, and whether the binaries' bytes
changed. M1 is not closed.

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
| `30-docker-ce.sh` | fetch the Docker CE key over https, verify it with `build_files/lib/verify-gpg-key.sh` (exactly one key, full-fingerprint pin `DOCKER_GPG_FPR`; a mismatch fails the build) and install it to `/etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce`; write inert `docker-ce.repo` (every section enabled=0, local `gpgkey=file://`, gpgcheck=1); remove `podman-docker`; install via `--enablerepo=docker-ce-stable`, including explicit `iptables` for the Docker/libvirt forwarding helper |
| `40-sysusers-fixup.sh` | **generic** orphan strip (keep only shadow/gshadow lines with a matching passwd/group) -> `systemd-sysusers` -> guarded `groupadd -r qemu` + `useradd qemu` + **`groupadd -r docker`**. Fixes virtqemud + docker.socket "Unknown group" boot failures. The most fragile piece; kept isolated on purpose |
| `50-docker-networking.sh` | intentional no-op: `ujust enable-docker` creates the Docker-only `iptable_nat` modules-load file locally |
| `60-libvirt-services.sh` | mask `libvirtd.service`; enable `virtqemud/virtnetworkd/virtnodedevd/virtnwfilterd/virtstoraged/virtproxyd.socket`; explicitly disable Docker service/socket; default NAT net autostart symlink (virsh can't run at build time); polkit rule moved to `system_files/usr/share/polkit-1/rules.d/49-bazzite-tower-libvirt-manage.rules` (manage = `AUTH_ADMIN_KEEP`, nothing written here); enable `bazzite-tower-firstboot.service` (kvm/libvirt only); enable `docker-libvirt-forwarding.path` / `.timer` |
| `62-host-services.sh` | enable `tailscaled.service` (no baked node identity or auth key; the operator runs `tailscale up`); explicitly disable `waydroid-container.service` so Waydroid stays a per-host choice |
| `64-endpoint-events-packages.sh` | dnf: rsyslog, rsyslog-mmjsonparse, rsyslog-gnutls for the opt-in Vigie endpoint-event feed; explicitly `systemctl disable` every unit those RPMs ship (no mask, no `|| true`), fails if `rsyslog.service` is absent or any unit is still enabled. Tested with PATH shims in `tests/test-endpoint-events-packages.py` |
| `70-guards-monitoring.sh` | Wi-Fi guard + SMART, opt-in Cockpit; install bpftool/sysstat/igt-gpu-tools/trace-cmd, explicitly disable sysstat.service + collect/summary/rotate timers and trace-cmd.service, retain sysstat.sleep bytes but force0644 to prevent unconditional suspend collection (intentional RPM mode override) |
| `80-ras-microcode.sh` | dnf rasdaemon (enable); **mask `mcelog.service`**; dnf microcode_ctl (latest) |
| `86-stall-detect.sh` | enable `bazzite-tower-stall-detect.service`: samples `CLOCK_MONOTONIC` and on a gap records which kernel workers sat in D state (the i915 GuC TLB-invalidation freeze leaves no watchdog trace) |
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
pinned Fedora 45 builder. Source, RPM toolchain inventory, licenses and binary
identity accompany the original release artifact. The installer extracts only
verified binary/licenses and stages system units from that same source; its
separate installed-overlay manifest hashes immutable files and records their
identity. No legacy release-install/global-user-enable script runs.

The system candidate also builds the pinned OpenSnitch source (upstream a1353848,
version 1.9.0, `daemonSource` in the policy, which must equal the bridge pins'
`submoduleCommit`) with its reviewed downstream repair, rebased onto a1353848.
It ships `allow`/`ebpf` with `Ebpf.ModulesPath` `/usr/lib/opensnitchd/ebpf`. Its BPF objects are compiled at image
build from the same a1353848 commit against BASE_IMAGE's kernel-devel (Track A
stages above; the RPM's v1.8.0 objects are overwritten); loading is
runtime-unverified until owner gates A and C (`bazzite-tower-opensnitch-ebpf-check --runtime` decides on the host). The same single patch (59 files) carries CPU fixes:
one process build per exec (ancestry is upstream's full walk), an exact
sock_diag lookup for connected TCP with the dump as fallback, names-only
`/proc/<pid>/fd` listing (the `/proc` pid order is upstream's), and GOMAXPROCS 4 unless `GOMAXPROCS` is set (`-workers` stays 16,
no unit change). Separate daemon provenance binds the patch, module
locks, generated protocol files, Go/protoc toolchain, RPM inventory, licenses
and actual `/usr/bin/opensnitchd` hash. Both manifests are checked by smoke
and boot tests; the boot check verifies the running daemon executable when
NFQUEUE is available. Rootless container checks cannot replace the VM gate.
Legacy builds retain upstream RPM extraction of the daemon and incur no native snitchwatch compilation (no scanner, bridge, daemon or snitchwatch checkout); only the per-kernel eBPF modules are built.
eBPF objects (`opensnitch.o`, `opensnitch-procs.o`, `opensnitch-dns.o`; the
native Go executable does not embed them): both profiles ship objects compiled at
image build. Legacy (the safe-pin and latest-kernel variants) builds them from OpenSnitch v1.8.0
(b404c4c6, `ebpf_prog` tree sha256 39512718…, fetched by the `ebpf-legacy-*` stages) because
its daemon is the upstream v1.8.0 release RPM: pairing is enforced by a reviewed
version-to-commit table in `snitchwatch-system-ebpf.py` plus the RPM pin in the pins
(`daemonRpm`: name, version, release, sha256), and `95-firewall.sh` runs
`ebpf.py check-rpm` on the downloaded RPM before extracting it, so an RPM bump that
is not re-pinned fails the build. Its manifest also records `daemonRpm`. (Intent
change: legacy used to carry the RPM objects and had to have no manifest.) The system profile ships objects compiled at
image build (Track A stages above) from the daemon's own OpenSnitch commit
(a1353848, pinned with its `ebpf_prog` tree sha256 in
`snitchwatch-system-ebpf-pins.json`, digest-anchored by
`EXPECTED_EBPF_PINS_SHA256` in `snitchwatch-system-ebpf.py`) against BASE_IMAGE's
own kernel-devel. Assembly stages them at `/usr/share/bazzite-tower/opensnitch-ebpf/`;
`95-firewall.sh` installs them over the RPM copies after the cpio extraction,
removes the staging dir and keeps `/usr/share/bazzite-tower/ebpf-manifest.json`
(commit, tree sha, kernel release, kernel config sha256, headers digest, clang,
toolchain NEVRAs, compile-stage RPM inventory sha256 (compile-stage-reported, informational, not verified provenance), per-object sha256). `bazzite-tower-opensnitch-ebpf-check` fails closed on a
missing/nonregular/non-BPF object and, for system, unless the manifest kernel is
the image's single `/usr/lib/modules/*` release, its config sha256 matches that
kernel's `config`, and every on-disk object hash matches (legacy: also unless the manifest commit is the pinned v1.8.0
commit and it records the pinned 1.8.0 daemon RPM (name, version, release, sha256) and the pinned tree sha; the helper does not verify the RPM itself, `check-rpm` at build time is the binding); smoke repeats these with `jq`/`sha256sum`. The portmaster VM spike has
no OpenSnitch, so `95-firewall.sh` drops the staged modules and manifest there. This is not proof that probes load on the running
kernel; the daemon falls back to `/proc` silently, so `ProcMonitorMethod: ebpf` is judged by `bazzite-tower-opensnitch-ebpf-check --runtime` (in readiness and `tower-health`).
Failure modes (deliberate, fail-closed; there is no degrade path): every legacy
image (`:latest`, `:latest-kernel`, safe-pin) now FAILS the build when its base
lacks a `kernel-devel` matching its `kernel-core` (the `ebpf-kernel` stage
refuses), so the weekly `base-bump.yml` PRs are exposed to this and a base-bump
build is expected to fail loudly there rather than ship stale objects. A
`FIREWALL_DAEMON=portmaster` build still runs the `ebpf-legacy-*` stages (it needs
that `kernel-devel` and a GitHub fetch of the pinned OpenSnitch commit) and then
discards the modules, which the portmaster spike does not use.
Offline tests: `tests/test-snitchwatch-ebpf.py` (guard fixtures, pins, tree hash,
header tar, handoff/compare, manifest, image check) and
`tests/test-snitchwatch-ebpf-legacy.py` (legacy pins/pairing, RPM pin and query
format, prep source-pin, assembly output shape, wiring), and
`tests/test-snitchwatch-resources.py` (ELF fixtures, interval accounting).

Only the system candidate changes the image-intent OpenSnitch address to
`unix:opensnitchd.sock`; both profiles permanently retain `DefaultAction: allow`
and `ProcMonitorMethod: ebpf` with `Ebpf.ModulesPath` pinned (`proc` is the documented fallback; stage/verify require ebpf + the pin, readiness accepts either and WARNs on proc). Deny enforcement requires explicit operator opt-in. Upstream `stage.sh` installs the
`opensnitch.service.d/snitchwatch-system-bridge.conf` drop-in (`Wants=`/`After=`
the gRPC socket, `WorkingDirectory=-/run/snitchwatch`), pinned by hash; it also
stages upstream's `ebpf` daemon config, which `snitchwatch-system-stage.py`
removes from the overlay (unit-tested); `95-firewall.sh` installs the tower's `allow`/`ebpf`
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
the scanner build uses a fresh `CARGO_HOME` (`$work/scanner-cargo`, before M1 step 3 `out/cargo-home`) rebuilt only from prep's
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
source-patch and environment-override cases. These checks do not establish actual root authorization, SELinux,
AI export or owner scan behavior (laptop testing is not a gate before a policy
repin);
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
1.100.0-1.fc45) to `snitchwatch-system-pins.json` for the bridge. The same helper
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
from r15; this repin selects the GUI learning/naming work. That candidate
pin did not by itself establish independent review or runtime acceptance.

The system profile remains PR-tested; `build.yml` does not automatically publish it. Accepted-product base refresh uses the separate policy-pinned path described above. The r13 candidate/promoter pipeline (`r13-candidate.yml`, `release.yml`, `ci/r13-candidate-data.py`) was retired on 2026-11-27; `ci/r13-publication.py` remains as the trusted-gate library for `ci/base-refresh.py`. Base-image/toolchain pins and smoke/boot assertions are preserved.

`tests/test-r13-publication.py` exercises the `ci/r13-publication.py` helpers; remote CI and runtime still require independent evidence.

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
