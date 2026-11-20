# Cloison: Qubes-style compartments on KVM for bazzite-tower

**Status:** PROPOSAL / design draft, rev 3 (2026-11-19: re-scope, **recommendations pending owner approval**; rev 2 and rev 1 2026-11-16, rev 0 2026-11-13). Only the M1 scaffold exists (PR #201, local build); nothing has been tested on the machine yet. "Cloison" (French for a partition or bulkhead) is a working name, and the owner can change it.
**Proposed location:** `docs/prp/cloison-kvm-compartments.md`
**Target:** ThinkPad P1 Gen 7 (Core Ultra 9 185H / Meteor Lake, RTX 4070 Max-Q on the open modules, Intel BE200, s2idle only)
**Decided by owner:** this is a **separate bootc image** in a **separate repository**, `ghcr.io/bearyjd/bazzite-tower-cloison` (D1), built from this repo and **built locally only until M3** (D5). It is not a mode or layer switch inside the daily-driver image. `:latest` stays untouched until the variant is proven.
**Convention:** *UNVERIFIED* marks a claim about this laptop, its firmware or the pinned package versions that nobody has checked on the machine. *verify* marks an upstream feature to check before relying on it.

### Rev 1 changelog: answers to the Codex critic (REWORK, 2026-11-16, `.omc/results/critic-cloison-plan-codex.md`)

| # | Sev | Finding (short) | Answered in |
|---|---|---|---|
| 1 | H | Rollback/state model wrong (`/etc` per-deployment, `rollback` ≠ daily) | §4.3 rewritten as a per-operation procedure (switch, upgrade, rollback, return-to-daily, offline GRUB); §4.5 state schema, backups and what survives a rollback; §5.7 credentials moved to `/var` |
| 2 | H | Signature-trust bootstrap missing; contradicts "never edits shared `/etc`" | §4.4 owner-run, reversible, digest-based bootstrap with negative tests; recorded as the one authorised `/etc` exception; template pulls use a Cloison-only policy file with default reject (§5.3) |
| 3 | H | "Every cross-domain byte" unenforced (vsock routes, CID reuse, stream binding) | §5.11 B2: per-backend route proof, host listener inventory, generation-bound identity, single-use transaction tokens, default-deny on unproven backends; §3 claim reworded |
| 4 | H | Daemon compromise ⇒ host control | §5.11 B1: root-owned helper allowlist, domain-name-only verbs, no paths or unit properties, credential map, no core dumps, PCR recovery, versioned protocol |
| 5 | H | Consent ≠ authorisation (clipboard race, launch DoS, extraction) | §5.11 B3 host-issued focus-bound clipboard grants; B4 launch quotas; B5 safe extraction protocol |
| 6 | H | GUI sandbox unspecified; sanitising arrives after M3 | §5.11 B6 sandbox spec + compromise-equivalent tests; sanitiser moves to M2 and wlproxy title rewriting is an M3 gate; "desktop TCB, not dom0" stated in §2 |
| 7 | H | DMA protection oversimplified | §2 row reworded; §7.1 evidence list (effective domain types, interrupt remapping, invalidation mode, TB tunnels at boot/hotplug/resume); D6 not decidable until captured |
| 8 | H | Unproven reset, boot-once vs suspend, auto-reclaim of a compromised BE200 | §7 decision tree: `reset_method` is evidence only, empirical reset + scope test, boot-once removed; §5.5 no automatic reclaim, host fallback is an explicit, labelled security downgrade; independent recovery uplink check |
| 9 | H | Template trust stops too early | §5.3 authenticated chain (signer, OCI digest, manifest, kernel, initramfs, rootfs), safe atomic install, downgrade/revocation/age policy; M2–M5 use only image-baked templates |
| 10 | H | Snapshot entropy acceptance unsound | §5.4: never-used pre-booted pool first; snapshots only behind a restore barrier; `boot_id` test removed |
| 11 | M | KSM launch-time only; core scheduling too narrow | §5.4: KSM enforced for the whole run with per-process audit; full scheduling trust group; SMT exclusion kept explicit (D7) |
| 12 | H | Roadmap gates contradictory | §8 reordered: M0 = preliminary topology only, hardware acceptance gates H-net/H-usb inside M9/M13, suspend rows split by device, M1 local only, §10.2 milestone-specific smoke contracts |
| 13 | M | Daily-image identity not established | §4.1 `cloison/parent.lock` (digest + source commit + profile + how verified); input identity defined by tree hashes, not output digests; M14 gets its own assertion contract (D10) |

### Rev 2 changelog: answers to the Codex re-critic of rev 1 (REWORK, 4 HIGH, PR #204)

| # | Sev | Finding (short) | Answered in |
|---|---|---|---|
| R1 | H | Digest-pinned install makes `bootc upgrade` a silent no-op | §4.3 Operations 2 and 4: every Cloison update is a new `cosign verify` + `bootc switch --enforce-container-sigpolicy …@sha256:<new>`; return-to-daily ends on daily's signed tracking tag; §5.10, §4.2; signed tracking tag offered as D13 |
| R2 | H | vsock isolation observational; listener check only blocks new launches | §5.11 B2: unproven backend gets **no vsock device** (helper-enforced, proof flag in the root allowlist, bound to VMM version); CH hybrid vsock only in v1, vhost-vsock only behind a host-wide bind restriction; tripwire stops **running** VMs |
| R3 | H | Template records written by guest-facing `cloisond` become helper launch authority | §5.3 + §5.1: separate `cloison-template-installer` (own uid/unit, no guest-facing parser) verifies and writes records; `cloisond` cannot write them; §4.5, B1, M6, §9 |
| R4 | H | Trust bootstrap circular (publish needs tests, tests need published image) | §4.4 split into three authorised steps: (a) publish non-bootable `trust-test-*` images, (b) bootstrap + tests against them, (c) only then publish the real image, with a pre-switch digest gate; §4.2, §8, D5, D14 |

Accepted risks that remain (not fixed by design): host kernel/KVM/VMM escapes; a large desktop host TCB (KWin, Plasma, NVIDIA module); side channels beyond D7; BE200 Bluetooth on the host USB stack; firmware persistence across a VM restart. Each is in §2.

### Rev 3 changes: re-scope after the 2026-11-19 evaluation (recommendations, not decisions)

Rev 3 is the re-scope that the owner decision doc calls "Cloison rev 2"; the in-file rev 2 above already names the R1–R4 answers. **Nothing below is decided.** D1 and D5 stay decided; D2–D4 and D6–D8 stay open. Sources are local owner files, not in the repo: **[eval]** `.omc/results/distro-evaluation-2026-11-19.md` (§3, rec 10); **[dec]** `.omc/results/decision-doc-2026-11-19.md` (items 1, 2, 4); **[dock]** `.omc/results/docker-group-analysis-2026-11-19.md`; **[owner]** evidence the owner pasted on 2026-11-19 (recorded in [dec] and the local STATE file).

| # | Change | Why | Source |
|---|---|---|---|
| C1 | §8: new **M1.5 spike**, two weeks, no new daemon, before M2; questions and exit rule in §8.1 | prove a daemon is needed before writing one | eval rec 10, dec 4 |
| C2 | §8: M12 split into **M12a** (USBGuard in the daily image, installed disabled with an allow+log config) and **M12b** (redirect to a VM, after M4) | logging is cheap and independent; enforcement and redirect need M4 `FileCopy` | eval §3, dec 2 and 4 |
| C3 | §8: **CUT** M13, M14 and M5's snapshot/restore | §8 reasons; both already defaulted out (D4, D10) | eval §3 |
| C4 | §8: **DEFERRED** M6, M8, M9, M10, M11, each with a revive trigger | cost for one owner versus the threat each one covers | eval §3 |
| C5 | §8: M7 (`dev`) no longer needs `cloisond` | `dev` can run on krun, Kata or libvirt alone | eval §3, dec 4 |
| C6 | §2, §11: the owner uid has passwordless root paths (`docker` group, libvirt `manage` twice, libvirt-dbus); what PR #225 changes | changes the assumptions under every host-side control | owner, dock, dec 1, PR #225 |
| C7 | §2: honest ceiling, and the Cloison guarantees that do **not** hold while `docker` membership remains | the plan named the session TCB but not these consequences | dock |
| C8 | §4.1, §11: parent profile and daily-refresh interaction | M1 was built on `:stable` (legacy product); the laptop follows `:snitchwatch-system` | STATE, README |
| C9 | §5.1, D2: how components authenticate to libvirt/polkit; daemon supply-chain recommendation | #225's prompt must not break the design; avoid the snitchwatch repin toil | dec 1 and 4, eval §3 |
| C10 | §5.4, §5.6, §7.2: folded in the sudo M0 run | step 1 passes; xHCI `00:14.0` carries the USB-A ports and internal devices | `docs/research/iommu-groups-p1g7-2026-11-16.md:29-40` |
| C11 | §12: D15–D19 added; D10 moot if M14 is cut; end of §12: owner-only questions | the re-scope creates new choices | this rev |

**Where rev 3 disagrees with the evaluation** (judgment, with reasons):
- **Other projects' pace is weak evidence.** The eval labels its Spectrum/Ghaf evidence [U] itself. The real argument is maintenance cost for one owner, and the spike measures that directly.
- **"Narrow libvirt to the `libvirt` group" (eval rec 6) changes nothing.** Upstream `50-libvirt.rules` already gives that group `manage` without a password [dec 1]. The eval also missed libvirt-dbus and the `docker` group. Closing libvirt alone buys little [dock].
- **A libvirt-based spike collides with #225.** Run as the desktop user against `qemu:///system`, it either accepts the cached prompt or measures a configuration the owner plans to remove. §8.1 Q5 makes the auth model a spike question.
- **"Consume a signed binary" is not free.** It needs a second signing identity (or the same key in a second repo), a `policy.json` scope, and revocation. Those are the M6 costs the eval cuts elsewhere. See D2.
- **The interim nftables is not "most of the control".** The plan labels it weaker, because the host kernel parses guest traffic (§5.5). Deferring M8 is fine for one user, but only as an accepted risk (D8).
- **USBGuard has no dry-run mode.** "Audit" means `ImplicitPolicyTarget=allow`, an empty rule set and `AuditBackend=FileAudit` [dec 2]. "Pull M12 forward" mixes low-risk logging with enforcement, and enforcement carries a high lockout risk.
- **Kata for app VMs is still rejected** (§5.4 table: shim + agent API in the host TCB, virtio-fs root share by default). The spike tries Kata/krun **only for `dev`**.
- **The M13/M14 cuts only remove text.** Both are already the defaults (D4, D10).

---

## 0. Executive summary

- **What it is.** An immutable Bazzite host stays the trusted base (the TCB). Untrusted work runs in KVM guests:
  - disposable app microVMs on Cloud Hypervisor
  - a persistent `dev` VM that replaces the `dev` distrobox
  - stateless `sys-net` (BE200 through VFIO), `sys-firewall` and `sys-usb` service VMs

  One Rust host daemon (`cloisond`) owns VM lifecycle, a qrexec-style policy engine, a narrow vsock RPC, and all data that crosses between domains. KDE gets thin front ends: launchers, KRunner, KWin colour rules, a tray/plasmoid, and trusted prompts.
- **How it ships.** It is a second image, `Containerfile.cloison`, built *on top of* a recorded, verified daily-driver digest (`cloison/parent.lock`, §4.1). So it shares the base pin, all of `build_files/`, `system_files/` and the smoke gate. It adds a variant-only overlay (`cloison/`) with its own smoke contract. Until M3 it is built and installed **locally** (D5); from M3 it gets its own signed tag after a one-time owner-run trust bootstrap (§4.4). You install it with `bootc switch` after recording the daily deployment. You return to daily by the explicit return-to-daily procedure in §4.3. A plain `bootc rollback` does not always do that.
- **Order of work (rev 3, pending approval).**
  1. Hardware topology (done) and the variant scaffold (merged).
  2. A two-week no-daemon spike (M1.5).
  3. Only if the spike says so: the daemon with its enforced boundaries, GUI and data crossing (M2–M4), never-used-pool launch (M5), and the `dev` VM (M7, which needs no daemon).
  4. USBGuard logging goes into the daily image separately (M12a); redirect follows M4 (M12b).
  5. Pulled templates, `sys-firewall` and `sys-net` are deferred; `sys-usb` passthrough and the slim host are cut (§8).

  Every milestone has its own acceptance tests and smoke contract.
- **The first gate is real.** The IOMMU layout is known from a non-root and a sudo M0 run (`docs/research/iommu-groups-p1g7-2026-11-16.md`). That is topology plus decision-tree step 1, not acceptance. Passthrough acceptance needs further evidence (§7.1). ACS override is never used.
- **Honest limits.** This does not defend against:
  - host kernel or VMM escapes (that includes the NVIDIA module and the KDE session)
  - side channels
  - device firmware that persists across VM resets (the BE200 Wi-Fi and Bluetooth functions share one card)
  - Meteor Lake s2idle quirks, which force passthrough VMs to be stopped and restarted around suspend
  - a compromised desktop host: the host stays a full KDE desktop TCB, not a Qubes-style minimal dom0

## 1. Goals and non-goals

**Goals**
1. Qubes-like compartments on KVM, with an immutable bootc host and a rollback for every change.
2. Stateless `sys-net` (BE200 through VFIO), `sys-firewall`, and `sys-usb` (controller passthrough where the groups allow it, otherwise per-device redirection with a USBGuard default-deny). Backends are pluggable. Secrets are injected from the host over vsock. USB storage gets a throwaway VM per device, with controlled copy-out.
3. Ephemeral microVMs: direct kernel boot, a read-only erofs template, a tmpfs overlay, a never-used pre-booted pool (snapshot/restore cut in rev 3), free-page reporting, and no KSM across domains.
4. KDE integration through one daemon, D-Bus plus CLI, and per-call policy. Clipboard and file copy are explicit, and every string that comes from a VM is sanitised.
5. Gradual migration: the daily driver is never put at risk.

**Non-goals for now**
- GPU acceleration in VMs. Rendering is software only; the NVIDIA and Intel GPUs stay on the host.
- Moving Bluetooth off the host.
- Audio and camera inside VMs. These are open questions (section 13).
- Matching Qubes' Xen-level host minimality. The host stays full Bazzite (the M14 slim host is cut in rev 3).
- Protection against physical access or evil-maid attacks beyond what Secure Boot and LUKS already give.
- Multi-machine support. As with the rest of the repo, this is for one laptop.

## 2. Threat model

**Assets:** the host and its session; the `dev` VM (code and tokens); credentials (Wi-Fi PSKs, SSH keys); data in each domain.

**Protects against (assuming the host kernel, KVM and the VMM hold):**

| Threat | Containment |
|---|---|
| Browser or document exploit in an app VM | Contained to that VM; disposables are destroyed on close |
| Malicious Wi-Fi frames hitting iwlwifi/iwlmld, wpa_supplicant or NM; rogue AP | Parsed by the `sys-net` guest kernel and userspace; reset on every `sys-net` restart |
| Lateral movement between app VMs over the LAN | `sys-firewall` and netns isolation; no L2 path between app VMs |
| BadUSB (HID injection), malicious USB firmware descriptors, hostile filesystem images | With controller passthrough, enumeration and filesystem parsing happen in `sys-usb` or a throwaway VM. With the fallback, host USBGuard default-denies, but the host kernel still enumerates the device (weaker; stated in section 5.6) |
| A VM spoofing host UI or another domain | Trusted title prefix and domain colour from the host proxy; fullscreen denied by default; host-styled prompts |
| A VM pulling data from the clipboard or other domains | No implicit clipboard; every transfer needs a host-issued, focus-bound, single-use grant (§5.11 B3) |
| Tampered templates at rest in `/var` | authenticated chain from signer to every payload file, atomic install, fs-verity checked at launch (§5.3) |

**Does NOT protect against (stated plainly):**
- **Host kernel bugs reachable from guests:** KVM, vhost-net, vhost-vsock, VFIO and the IOMMU driver.
- **VMM escapes:** Cloud Hypervisor or QEMU device models, virtiofsd, waypipe. A process sandbox only limits the blast radius.
- **The host TCB is large:** the NVIDIA open kernel module, KWin and the whole Plasma session, the Wayland filter proxy, and the daemon's parsers. This is a desktop host, not a Qubes dom0, and no milestone claims dom0 equivalence.
- **Side channels:** Spectre-class, cache and timing, and SMT sibling sharing (see D7). Balloon free-page reporting leaks coarse memory-usage patterns. Shared page cache (DAX) is deliberately avoided.
- **Device firmware persistence.** The BE200's runtime state can survive a VM restart if the reset is incomplete. **The BE200's Bluetooth function stays on the host's USB stack.** A Wi-Fi-side firmware compromise could therefore attack the host through the Bluetooth/USB path; isolation of the combo card cannot rule this out. USB device firmware is untouched by a VM reset.
- **DMA from host-owned devices.** With the inherited `iommu=pt` (`system_files/usr/lib/bootc/kargs.d/00-iommu.toml`), host-owned devices may sit in identity domains, so a malicious host-side device can DMA into host memory. Dropping `iommu=pt` alone would not prove protection either. Translation (domain type) and invalidation strictness (`DMA` vs `DMA-FQ`) are separate controls, and Thunderbolt adds firmware-created tunnels. The effective state is *UNVERIFIED* until the §7.1 evidence is captured (D6).
- **Compromise of the `dev` VM.** Anything the owner puts in `dev` is exposed if `dev` falls. Persistence is a feature here, not isolation.
- **Traffic analysis, supply chain upstream of signed templates, a malicious owner, physical attacks.**

**Rev 3: root paths of the owner uid, with evidence.** Cloison contains *guests*. It does not contain code that runs as the owner on the host.

What the owner reported on 2026-11-19 [owner, via dec 1]:
- Groups: `wheel`, `libvirt`, `docker`, `snitchwatch-ui` (not `empower`).
- `pkcheck` exits 0 for both `org.libvirt.unix.manage` and `.monitor`, so both are passwordless.
- `ostree admin config-diff` shows no local polkit edits.

The paths, each checked on main:
- **`docker` group = root-equivalent.** It is a host opt-in, not an image default. `ujust enable-docker` adds it (`system_files/usr/share/ublue-os/just/60-custom.just:111`) and re-adds it on every run. Firstboot never adds it (`tests/test-firstboot-no-docker.sh:32`).
- **libvirt `manage` is root-equivalent.** A client with it can define a domain with a host block device or filesystem. It is passwordless by two rules:
  1. our `wheel` rule (`build_files/build.d/60-libvirt-services.sh:41-48`);
  2. upstream `/usr/share/polkit-1/rules.d/50-libvirt.rules` for group `libvirt`, which firstboot grants (`system_files/usr/libexec/bazzite-tower-firstboot:25`).
- **A third libvirt path: libvirt-dbus**, pulled in by `cockpit-machines` (`build_files/build.d/70-guards-monitoring.sh:41`). The daemon runs as `libvirtdbus` with `manage` = YES and does not check the real caller, so any `libvirt`-group process gets silent read-write `qemu:///system` over the system bus (PR #225 security review; not re-checked here).

**Merged vs pending.** On main today **all three libvirt paths and the `docker` path are live**. Draft PR #225 is **OPEN, not merged** (`gh pr view 225`, 2026-11-19). It would:
- add `49-bazzite-tower-libvirt-manage.rules`, returning `AUTH_ADMIN_KEEP` for `wheel`/`libvirt`. It sorts before upstream's rule and so closes paths 1 and 2. Whether real polkitd behaves that way on the laptop is *UNVERIFIED*.
- drop `cockpit-machines`, which removes the libvirt-dbus path.
- document rootless Docker. Leaving the `docker` group is a **host step** (`docker context use podman`, then `gpasswd -d`) that no PR can make.

[dock]: #225 alone gains little while the user stays in `docker`.

**The honest ceiling.** With all four closed, the result is "root at the next `sudo`": session code can wrap `sudo` in a shell rc file and capture the password. This plan already puts the Plasma session in the TCB, so no *stated* guarantee breaks. Stated plainly, these controls do **not** hold against owner-uid code while `docker` membership (or any passwordless path above) remains:
- the §4.4 trust bootstrap "with sudo, interactively". Silent root can rewrite `/etc/containers/policy.json` or the `registries.d` file before or after it runs;
- polkit admin auth on `--allow-downgrade` and on the template installer (§5.3), because root can write `/var/lib/cloison` directly;
- the integrity of `/var/lib/cloison` (state, credentials, template records), `/etc/cloison` policy and domains, the root-owned B1 allowlist (§5.11), and the deployment itself (`bootc switch`);
- OpenSnitch/Snitchwatch as a boundary against that code ([dock] iii).

What still holds: guest-to-host containment (it needs a VM escape), guest-to-guest isolation, and the GUI and clipboard rules against guests. A hostile repo built **on the host** is root today. Built **inside `dev`** (M7) it is contained, as long as `dev` gets no host Docker or libvirt socket. That makes M7 the milestone that does most against this finding.

## 3. Architecture overview

```
                         ┌──────────────── HOST (bazzite-tower-cloison, bootc, TCB) ────────────────┐
                         │ KDE Plasma/KWin (Wayland) ── cloison-session (user svc: tray, KRunner,    │
                         │        ▲ wl_shm only          prompts, notifications, clipboard UI)       │
                         │        │                         │ D-Bus (session → system)               │
                         │ cloison-wlproxy@<dom> ◄─ waypipe client (sandboxed, per domain)           │
                         │        ▲                         ▼                                        │
                         │        │ vsock            cloisond (unpriv user, policy engine, RPC relay)│
                         │        │                         │ varlink (narrow)                       │
                         │        │                  cloison-helper (root: VFIO bind, netns/taps,    │
                         │        │                   systemd transient units, fs-verity checks)     │
                         │  netns "cloison" (no host IP on VM links; Docker/br_netfilter not here)   │
                         │   br-net ── br-fw-<app> ...   veth ↔ root netns "cloison-up" (host uplink)│
                         └────┬───────────┬──────────────┬───────────────┬──────────────────────────┘
               VFIO (BE200)   │           │ virtio-net    │ virtio-net    │ VFIO xHCI or usb-host redirect
                     ┌────────▼───┐  ┌────▼───────┐  ┌────▼────────┐  ┌───▼─────────┐
                     │ sys-net    │◄─┤sys-firewall│◄─┤ app VMs     │  │ sys-usb     │──► usb-disp-N
                     │ NM+iwlwifi │  │ nftables   │  │ disp-*, dev │  │ (stateless) │   (per storage
                     │ stateless  │  │ stateless  │  │ CH microVMs │  │             │    device)
                     └────────────┘  └────────────┘  └─────────────┘  └─────────────┘
   all guests: cloison-agent on vsock :5000 (RPC), waypipe server on vsock :5100 (GUI-capable only)
   design rule: no guest↔guest vsock; cross-domain data goes only through cloisond relays. This holds only on a
   backend whose routing has been proven by the §5.11 B2 tests; unproven backends get no vsock device at all (default deny)
```

**Key choices**

- **Domain identity comes from the host, never from guest claims.**
  - With kernel vhost-vsock, identity is the vsock source CID, which the kernel stamps.
  - With Cloud Hypervisor's userspace vsock, identity is which per-VM host Unix socket the connection arrived on.
  - Whether CH's vsock is the hybrid Unix-socket backend is *to verify*.
- **All VM networking lives in a dedicated `cloison` network namespace.** The root netns, Docker's `DOCKER-USER`/`FORWARD DROP`, `br_netfilter`, OpenSnitch and `docker-libvirt-forwarding` never see VM links. The host's own uplink is a veth from that namespace.
  - Whether the `bridge-nf-call-*` sysctls are per-netns on the pinned kernel is *to verify*; the design doesn't depend on it.
- **VMM split.** Cloud Hypervisor runs app microVMs and is the first choice for service VMs, since CH supports VFIO. QEMU through libvirt (already in the image) is the fallback backend for `sys-net`/`sys-usb` if CH's VFIO path misbehaves with the BE200 or the xHCI. crosvm is an optional later backend for virtio-gpu cross-domain.
- **Process supervision is systemd.** Each VM is `cloison-vm@<name>.service`, giving cgroup memory and CPU limits, `DynamicUser=`, `DeviceAllow=` and seccomp.

## 4. How the separate image is built, published, installed and rolled back

### 4.1 Build: stacked on the daily image from the same commit

```
Containerfile          (unchanged)  FROM ${BASE_IMAGE pin} ... -> bazzite-tower:<tag>
Containerfile.cloison  (new)        ARG PARENT_IMAGE   # exact digest of the image above
                                    FROM ${PARENT_IMAGE}
                                    COPY cloison/system_files/ /
                                    COPY --from=cloison-daemon /out/ /       # pinned, verified build stage
                                    COPY --from=cloison-templates /out/ /usr/lib/cloison/templates/
                                    RUN cloison/build.sh                     # cloison/build.d/*.sh in order
                                    RUN bootc container lint
```

- **Shared base pin, recorded parent.** The variant never names a Fedora/Bazzite base. Its parent is one **recorded, verified daily digest** in `cloison/parent.lock`:

  ```json
  { "digest": "sha256:…", "source_commit": "<40-hex>", "profile": "safe-pin",
    "verified_by": "local-build-clean-tree | cosign-verify ghcr.io/bearyjd/bazzite-tower@sha256:…",
    "recorded": "YYYY-MM-DD" }
  ```

  - **Local (M1–M3, D5):** `just build` on a clean tree at `source_commit`. The tree must be clean, so that `SHA_HEAD_SHORT` is set and the image's `org.opencontainers.image.revision` label names the commit. Then record the resulting local digest.
  - **Published (from M3):** a `ghcr.io/bearyjd/bazzite-tower@sha256:…` that passes `cosign verify --key cosign.pub` and whose revision label equals `source_commit`. A tag such as `:latest` is never the input. A tag only names "whatever was pushed last", which need not be the same source or profile.
  - `just build-cloison` refuses when the parent's revision label or digest disagrees with the lock. The weekly `base-bump.yml` keeps covering the base pin. Moving the parent is a reviewed one-line lock change. The variant is never built on `:latest-kernel`.
- **Rev 3: what the parent really is today (verified on main unless marked).**
  - `cloison/parent.lock` **does not exist yet**. The M1 scaffold takes the parent as `just build-cloison <parent@sha256> <commit> <profile>` and records it in labels (`Justfile:152`, `docs/CODEMAPS/cloison.md:24,31`). The lock above is the M2 design.
  - The published channels come from `base-refresh.yml`: `:stable`/`:latest` carry the **legacy** product and `:snitchwatch-system` the **system** product (README:26, README:360-376). The laptop follows `:snitchwatch-system` (STATE). M1 was built on a `:stable` digest (STATE). A switch to that variant would therefore also move the laptop to the legacy firewall product. **Recommendation (D19):** the parent is a digest of the channel the laptop follows. The rev 2 `"profile": "safe-pin"` example predates the refresh channels.
  - A published image's `org.opencontainers.image.revision` label is the **base image's**, not tower's (STATE: `5d71c437` on images built from tower `5eefa283`; not re-checked here). "Revision label equals `source_commit`" therefore cannot be checked on a published parent. Use the base-refresh run record (policy `sourceCommit`) and `cosign verify` instead.
  - **Refresh interaction.** Parent digests move on every daily refresh. While booted into Cloison, the laptop gets **no** base, kernel or snitchwatch update until the owner rebuilds on a newer recorded parent and switches by digest (§4.3 Operation 2). This freezes the kernel, which helps with gate C, but security fixes lag. D19 sets a rebuild cadence. A parent bump that moves the kernel re-runs suspend row S1. Toolchain repins happen in the parent, so the variant has no toolchain pin of its own unless D2 chooses a source build.
- **Shared versus variant-only content.**
  - All existing `build_files/build.d/*` and `system_files/*` are inherited through the parent, unchanged.
  - Variant content lives only under `cloison/` (its own `build.d/`, `system_files/`, `guests/` and pins).
  - **Daily identity is defined on inputs, not output bytes.** Image builds are not reproducible here (timestamps, package mirrors), so "daily digest-identical" is not a usable test. Instead, `tests/test-cloison-isolation.sh` asserts two things. First, the git tree hashes of `Containerfile`, `build_files/`, `system_files/` and `Justfile` daily recipes are unchanged by a Cloison-only PR (`git diff --quiet origin/main -- <paths>`). Second, nothing under those paths references `cloison/`.
- **Parent traceability.** The parent digest, source commit and profile go into OCI labels (`io.github.bearyjd.cloison.parent`, `.parent-revision`, `.parent-profile`) and into `/usr/share/cloison/parent.lock`. Smoke asserts that they are present and equal to the lock.
- **Why stacked rather than a separate full Containerfile.** It is one pin, one place where the daily customisations are proven, the variant can't drift from the daily image, and the variant diff stays small and reviewable. The M14 slim host is the exception: it gets its own full Containerfile from the same base pin (see M14).

### 4.2 Build and CI path (local only until M3, D5)

| Stage | Daily image (unchanged) | Variant, M1–M2 (local) | Variant, from M3 (after the D5 publish gate) |
|---|---|---|---|
| Build | `build.yml` legs `safe-pin`, `latest-kernel`, `snitchwatch-system` | `just build-cloison` on the laptop against `parent.lock`. **No CI image build, no GHCR push** | CI leg or `build-cloison.yml`, parent pulled **by digest** from the lock and cosign-verified |
| Static CI | unchanged | shellcheck, `just check`, hadolint on `Containerfile.cloison`, isolation test, policy fixtures, pins verifier, smoke-contract lint (§10.2) | same |
| Smoke | `tests/smoke.sh` | `just smoke-cloison` locally: `tests/smoke.sh` (every daily assertion), then `tests/smoke-cloison.sh` for the current milestone contract | same, as the CI gate |
| Boot test | `boot-test.yml` → `tests/boot-check.sh` | local `podman run --systemd=always` with `tests/boot-check-cloison.sh` | the `boot-test.yml` harness |
| VM-level | n/a | `tests/vm/*.sh` on the laptop | same (CI only if a KVM runner exists; *verify*) |
| Publish | `release.yml` → cosign sign + SBOM | none | three separately authorised steps (§4.4): (a) `trust-test-*` images only, (b) bootstrap + tests on the laptop, (c) only then the real image: own candidate tag, cosign sign by digest with the same key, `release` environment. Each release records its digest; the laptop moves to it by a verified digest switch (§4.3 Operation 2), never by `bootc upgrade` |

The publish leg is added only in the M3 PR. Adding it changes a workflow, so it needs the owner's explicit release authorisation (`docs/release-playbook.md`).

### 4.3 Installing it next to the daily driver

**State model (what the procedures rely on).** These are ostree/bootc semantics. Check each against `man bootc-switch`, `man bootc-rollback` and `man ostree-admin-pin` on the installed versions before M1 (*verify*).
- **`/var` is one shared copy.** No deployment operation touches it. Cloison volumes, snapshots, templates and state survive every switch, upgrade and rollback, in both directions.
- **`/etc` is per-deployment.** When a *new* deployment is created (`switch`, `upgrade`), the current `/etc` is 3-way merged into it. Locally modified files win over image defaults. Changes made while booted into deployment B never reach an *older* existing deployment A. `rollback` and a GRUB pick boot an **existing** deployment with the `/etc` it already had.
- **Kernel arguments are per-deployment** (from that image's `kargs.d`). The daily deployment never gets Cloison kargs.
- **`bootc rollback` swaps to the previous deployment, not to "daily".** After one Cloison upgrade the previous deployment is an older Cloison, so the daily one survives only by being **pinned**. Pin indexes shift as deployments come and go. The daily deployment is therefore always identified by **ostree checksum + image digest**, never by index.

**Operation 0: record and pin daily (once, before the first switch).**
```bash
rpm-ostree status --json | sudo tee /var/lib/cloison-daily-record.json   # the read is unprivileged; keep a printed copy too
# record: booted deployment checksum, tracking image ref (tag) + digest, version string shown in GRUB
sudo ostree admin pin <index-of-that-checksum>                  # index looked up from the same status output, not assumed 0
```
The record lives outside `/var/lib/cloison/` so `cloison` tooling cannot overwrite it. The owner also writes the GRUB title/version string on paper, for Operation 5.

**Operation 1: switch to Cloison.**
- M1–M2 (local, D5): `sudo bootc switch --transport containers-storage localhost/bazzite-tower-cloison@sha256:<recorded>`. **No signature check applies to this transport.** Trust rests on "built on this machine from `parent.lock` by the owner", and the digest the owner switches to is the one `just build-cloison` printed. `--transport containers-storage` support on the installed bootc is *verify*.
- From M3: `sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower-cloison@sha256:<digest>`. This is allowed only after all three §4.4 steps are done, and only **from the booted deployment that received the bootstrap** (daily). A local Cloison deployment created in M1–M2 before the bootstrap lacks the scope, so `podman image trust show` must list the Cloison scope immediately before the switch. Without §4.4 the repository falls through to the `docker` transport's `insecureAcceptAnything` fallback, and the switch would accept an unsigned image.
- Effect: a new deployment whose `/etc` is merged from daily's. Reboot.

**Operation 2: update Cloison (a new verified-digest switch, every time).** A deployment installed from `…@sha256:<digest>` tracks that digest only. `bootc upgrade` and `bootc upgrade --check` then report nothing new forever, so they are **not** used, and "no update found" from them means nothing.
1. Learn the new digest from the release record (the publish job prints it; from M3).
2. `cosign verify --key /etc/pki/containers/bazzite-tower-cosign.pub ghcr.io/bearyjd/bazzite-tower-cloison@sha256:<new>` passes, and the digest's `parent` label equals a `parent.lock` the owner accepts.
3. `cloison state backup` (§4.5).
4. `sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower-cloison@sha256:<new>`, reboot, and confirm with `bootc status` that the booted digest is `<new>`.
- M1–M2 (local): the same, with `--transport containers-storage localhost/…@sha256:<new>` and step 2 replaced by "the digest `just build-cloison` printed".
- Alternative (D13): switch once to a **signed tracking tag** (for example `:cloison-candidate`) under the enforced policy, after which `bootc upgrade` works. Default is the digest switch, because a tag lets whoever can push it choose what the laptop boots next.
- Afterwards the rollback slot is the previous **Cloison**, not daily.

**Operation 3: roll back Cloison.** `bootc rollback` returns to the previous Cloison deployment. `/var/lib/cloison` is **not** rolled back. The older daemon must read the newer state, or refuse to start (§4.5). Rolling back never restores template, volume or snapshot contents. Use `cloison state restore <backup>` for that.

**Operation 4: return to daily (online).** In order of preference:
1. If daily is the current rollback slot (no Cloison upgrade since the switch), use `bootc rollback`, then confirm with `rpm-ostree status` that the booted checksum equals the recorded one.
2. Otherwise run `sudo bootc switch --enforce-container-sigpolicy <daily's recorded tracking ref>` (the signed tag daily normally follows, for example `ghcr.io/bearyjd/bazzite-tower:latest`, taken from the Operation 0 record), then confirm with `bootc status` that the booted digest equals a `cosign verify`-ed daily digest. **Do not** return to daily by `@sha256:` digest: that would pin daily and silently stop its own updates. This creates a **new** daily deployment whose `/etc` is merged from Cloison's. `/etc/cloison/` and `/etc/usbguard/` come along, inert, because daily ships neither consumer. This needs network: run `cloison net mode host` first (§5.5).
3. Making the pinned deployment the default in place (`ostree admin set-default` or similar) is *UNVERIFIED* on this ostree version and is not part of the procedure until checked.

**Operation 5: offline recovery from GRUB.** If Cloison does not boot to a usable session, pick the pinned daily entry by the recorded version string in the GRUB menu. Bazzite may hide the menu, and the key to reveal it on this laptop is *UNVERIFIED* (try Esc or Shift). That entry boots with daily's own `/etc` and kargs and ignores `/var/lib/cloison`. No VFIO binding survives a reboot. Then use Operation 4.2 to make daily the default. This path is drilled in M1 acceptance.

**Rules for the variant's footprint.**
- Persistent Cloison state goes **only** in `/var/lib/cloison/` (shared, schema-versioned, §4.5). `/etc/cloison/` holds only declarative owner config (domains, policy, labels), which moves with the deployment like any `/etc` file.
- Credentials live in `/var/lib/cloison/credentials/`, not `/etc` (§5.9). Otherwise a rollback to an older deployment would boot with stale sealed secrets.
- Runtime NetworkManager profiles (the host uplink veth) go in `/run/NetworkManager/system-connections`, never in `/etc`.
- The variant edits no shared `/etc` file (NM conf, logind, `containers/policy.json`). The **one exception** is the owner-run signature bootstrap in §4.4, which happens on the **daily** deployment, by the owner, with a documented revert.
- USBGuard rules in `/etc/usbguard/` are inert in daily, because daily does not ship usbguard. VFIO bindings and `driver_override` are runtime only.
- Every milestone's acceptance includes Operations 4 and 5 (M1 drills both).

### 4.4 Signature-trust bootstrap for the new repository (owner-run, once, before the first remote switch at M3)

**Order: three separately authorised steps, so nothing is circular.**
- **(a) Test artifacts.** The owner authorises one publish of **test images only**, under the tag prefix `trust-test-*` in the real repository `ghcr.io/bearyjd/bazzite-tower-cloison`. Using the real repository is deliberate: a separate test repository would not exercise the real scope's `matchRepository` binding. Each test image is `FROM scratch` with one label file. It has no kernel or ostree commit, so a mistaken `bootc switch` to one fails. The set is `trust-test-unsigned`, `trust-test-otherkey` (signed with a throwaway key), `trust-test-wrongrepo` (the daily image's signature copied with `cosign copy`) and `trust-test-signed` (signed with the real key). Who pushes and signs them, and where (owner on the laptop, or a dispatch-only job in the M3 PR), is D14. No agent uses the local `cosign.key`.
- **(b) Install trust and test.** Do steps 1–3 below on daily, then run the test table below against the `trust-test-*` tags. No real Cloison image is needed or exists yet.
- **(c) Real image.** Only after (b) passes, the owner separately authorises publishing the real Cloison image (§4.2). Before the first remote switch, its digest must be accepted by `skopeo copy` under the installed policy and pass `cosign verify`. This is the pre-switch gate in §4.3 Operation 2, step 2.

**Why it is needed.** The shipped `/etc/containers/policy.json` (from `system_files/usr/share/bazzite-tower/containers/policy.json`, installed by `15-signature-policy.sh`) has a `sigstoreSigned` scope only for `ghcr.io/bearyjd/bazzite-tower`. The top-level default is `reject`, but the `docker` transport keeps a `""` compatibility fallback of `insecureAcceptAnything`, and `tests/smoke.sh` asserts that fallback on purpose. Scopes match on repository path components, so `ghcr.io/bearyjd/bazzite-tower-cloison` is **not** covered today. A signature-"enforced" switch would accept anything. The daily deployment must trust the new repository **before** the first enforced switch, and an image cannot install its own trust.

**What the owner runs (on the booted daily deployment):**
1. **New file, not an edit:** `/etc/containers/registries.d/bazzite-tower-cloison.yaml` with `use-sigstore-attachments: true` scoped to exactly `ghcr.io/bearyjd/bazzite-tower-cloison`.
2. **The one shared-`/etc` edit:** add one scope to `/etc/containers/policy.json`: `"ghcr.io/bearyjd/bazzite-tower-cloison": [{"type":"sigstoreSigned","keyPath":"/etc/pki/containers/bazzite-tower-cosign.pub","signedIdentity":{"type":"matchRepository"}}]`. The key file already exists on the daily deployment. The repository identity is exact, with no wildcard.
3. Validate the file with `python3 -m json.tool`. Keep a copy of the pre-edit file as `/etc/containers/policy.json.pre-cloison`.

A `cloison/bootstrap-trust.sh` script will do steps 1–3 and the tests below. The owner reviews and runs it, with sudo, interactively. No image, unit or CI job runs it.

**Negative and positive tests (step b; all must pass before step c is authorised):**
| Test | Expect |
|---|---|
| Before the bootstrap: `podman image trust show`, and `skopeo copy docker://…cloison:trust-test-unsigned dir:/tmp/x` | default (`insecureAcceptAnything`) and **accepted**. This proves the gap and that the test can see it |
| After: `trust-test-unsigned` | refused |
| After: `trust-test-otherkey` | refused |
| After: `trust-test-wrongrepo` | refused (`matchRepository` binds the signed identity) |
| After: `trust-test-signed` | accepted (a test image, not the real one) |
| After: `bootc switch` to `trust-test-signed` | fails (non-bootable by construction) |

**Consequences, stated honestly.**
- Once `policy.json` is locally modified, the 3-way merge keeps the local file. **Later daily-image changes to `policy.json` stop landing** on this machine until it is reverted. `tests/smoke.sh` checks only the policy shipped in the image, so it cannot see this host-side drift. Mitigation: the bootstrap script prints a diff between the image default and `/etc` on every run. M3 also decides whether the Cloison scope moves **into the daily image's** `policy.json` through a normal reviewed PR. That retires the exception (D11).
- **Revert:** restore `/etc/containers/policy.json.pre-cloison` (or the image default; whether a pristine copy exists under `/usr/etc` on this host is *UNVERIFIED*) and delete the `registries.d` file. Then re-run the "before" test.
- The `registries.d` file and the policy scope also apply to later Cloison deployments through the merge. That is intended.

### 4.5 `/var/lib/cloison` state: schema, backups, and what survives a rollback

| State | Survives OS rollback? | Rule |
|---|---|---|
| `state-version` + daemon DB (domain registry, grants log). Template records are **not** here: they belong to the installer (§5.3) | yes (shared `/var`) | **major.minor** schema. A daemon reads N and N-1. Minor bumps are additive only. A daemon that finds a **newer major** starts read-only (lists, no launches) and says so. Migrations write N only after `cloison state backup` succeeds |
| Private volumes `domains/<name>/private.img` | yes | never migrated by the host. `cloison state backup` takes a reflink copy (`cp --reflink=always`; behaviour on `chattr +C` files is *verify*) before every Cloison upgrade and keeps 2 |
| Pulled templates `templates/<name>/<digest>/` + records `template-records/` | yes | content-addressed, so an older daemon simply sees extra digests. Owned by `cloison-tinst` (§5.3); `cloisond` has read-only access. Records carry their schema version |
| Credentials `credentials/*.cred` | yes | sealed format versioned. Re-seal after a PCR change (§5.9) |

`cloison state backup|restore|list` ships in M2. Restore needs the daemon stopped, and it never touches the OS deployment.

## 5. Components

### 5.1 Control plane: `cloisond`, `cloison-helper`, `cloison` CLI, `cloison-session`

| Process | Runs as | Responsibilities | Exposure |
|---|---|---|---|
| `cloison-helper` | root, socket-activated, tiny | VFIO bind/unbind (`driver_override`), tap/bridge/veth in the `cloison` netns, starting `cloison-vm@` transient units, fs-verity checks, USB authorize/redirect | varlink socket readable only by the `cloison` group; verbs take a **domain name + generation only** and resolve everything else from root-owned config (§5.11 B1) |
| `cloisond` | system user `cloison`, seccomp, `ProtectSystem=strict` | domain registry (`domains.d`), policy engine, vsock RPC relay, template **listing** (read-only), secrets injection, logind delay inhibitor | system bus `io.github.bearyjd.Cloison1` with polkit actions per method; vsock to guests |
| `cloison-template-installer` (M6) | system user `cloison-tinst`, own socket-activated unit, `ProtectSystem=strict`, network only to the registry | pulls, verifies, extracts and atomically installs pulled templates; sole writer of `/var/lib/cloison/templates/` and `template-records/` (§5.3) | varlink socket for the CLI only, every call gated by polkit admin auth. **No vsock, no guest-facing parser, no connection from `cloisond`** |
| `cloison` | user | CLI (`cloison ls/start/stop/run/copy/usb/net/template/policy check`) | calls D-Bus |
| `cloison-session` | user service in Plasma | tray (SNI), KRunner D-Bus runner, trusted prompts, notification forwarding, clipboard hotkeys, per-domain `waypipe` client and `cloison-wlproxy` units | session bus only |

- **Language and supply (D2, open).** Rust, AGPL-3.0. This applies only if the M1.5 spike says to build a daemon. Options:
  - **(a) Snitchwatch pattern** (rev 2 default): a separate repo consumed as a sha256-pinned source tarball, rebuilt offline in a `cloison-daemon` stage. It is reproducible and needs no new trust root. Cost: the same repin toil as snitchwatch. Its toolchain RPMs are pinned by NEVRA separately from the source pin, and the repin tool does not move them (STATE lesson, #218).
  - **(b) Signed binary or RPM from the daemon repo's CI** (the eval's advice). It keeps build machinery out of this repo. Cost: a second signer identity (or the same key in a second repo), a `policy.json`/verify step for it, revocation, and trust in that repo's CI and branch protection. A COPR RPM moves trust to COPR's key.
  - **(c) Vendored source in this repo** under `cloison/daemon/`, built in a stage. No pins and one review surface; this repo gets bigger.
  - **Rev 3 recommendation:** (c) until M4 is accepted (one maintainer, and the spike may kill the daemon), then reconsider (b) at the first publish. Not (a).
- **Authentication to libvirt and polkit (rev 3, so #225's prompt does not break the design).**
  - The v1 VMM is Cloud Hypervisor, driven by `cloison-helper` (root) through transient units, so it needs no libvirt.
  - If a QEMU/libvirt backend is used (only the deferred `sys-net`/`sys-usb` fallback), **only the root `cloison-helper`** opens `qemu:///system`, under B1 domain-name verbs. Root unix clients are not subject to libvirt's polkit check (*UNVERIFIED* on the laptop's libvirt 12.6), so the `manage` prompt never appears in that path.
  - `cloisond` (user `cloison`, in neither `wheel` nor `libvirt`) is **never** a libvirt client. No rule may grant it `manage`: that would make the guest-facing process root-equivalent.
  - Users reach `cloisond` only through `io.github.bearyjd.cloison.*` polkit actions, which `cloisond` checks against the D-Bus caller (§5.11). Proposed defaults: routine disposable start/stop `allow_active`; policy edits, net downgrade and template install `auth_admin_keep`.
  - The M1.5 spike runs as the desktop user, so under #225 it hits the cached prompt, unless it uses CH run as the user (needs only `kvm`) or `qemu:///session` (§8.1 Q5).
- **Users.** New system users come from `cloison/system_files/usr/lib/sysusers.d/cloison.conf`. Re-run the materialisation the way `40-sysusers-fixup.sh` does (the orphan-shadow workaround), plus a smoke assertion that `getent passwd cloison` resolves.

### 5.2 vsock RPC and policy

**Transport.** Each guest's `cloison-agent` listens on vsock port 5000.

- **Framing:** `u32 BE length ‖ JSON`.
  - Control frames are capped at 64 KiB.
  - JSON is parsed with `deny_unknown_fields`, and an over-length or invalid frame drops the connection.
  - After a call is allowed, the daemon may open a separate **data stream** (raw bytes, byte-counted, capped per policy). This is the qrexec split between control and data. Each stream is bound to a single-use transaction token (§5.11 B2).
  - Every frame carries a protocol `version`. The agent and daemon negotiate the highest common version at connect and refuse below a floor.
- **Guests never address each other.** A call names `service`, `+argument` and `target`. The daemon resolves the target, evaluates policy, and (if allowed) opens the stream to the target's agent and splices the two.

**Policy format.** This is Qubes policy v5 style: first match wins and there is an explicit default deny. Image defaults are `/usr/share/cloison/policy.d/*.policy`; owner overrides are `/etc/cloison/policy.d/*.policy`, which sort first. `cloison policy check` validates both (in CI too).

```text
# /usr/share/cloison/policy.d/50-default.policy
# service                 +arg   source            target               action  params
cloison.Notify            *      @tag:gui          @host                allow   rate=10/60s
cloison.Clipboard.Offer   *      @anyvm            @host                grant   max_bytes=1M      # needs a host copy grant (§5.11 B3)
cloison.Clipboard.Paste   *      @host             @anyvm               grant   max_bytes=1M      # needs a host paste grant (§5.11 B3)
cloison.FileCopy          *      @tag:usb-storage  @tag:app             ask     max_bytes=4G max_files=10000
cloison.FileCopy          *      @anyvm            @anyvm               ask     max_bytes=4G max_files=10000
cloison.OpenURL           *      @tag:app          @dispvm:disp-browser allow   quota=launch       # §5.11 B4
cloison.OpenInDisp        *      @anyvm            @dispvm              allow   quota=launch
cloison.net.Status        *      sys-net           @host                allow   rate=2/1s
cloison.net.Scan          *      @host             sys-net              allow
cloison.net.Connect       *      @host             sys-net              allow
cloison.secrets.Fetch     wifi   sys-net           @host                allow   once_per_generation=yes
cloison.usb.Attach        *      @host             sys-usb              allow
cloison.Exec              *      @host             dev                  allow   user=user
cloison.Exec              *      @host             @anyvm               ask
cloison.Exec              *      @anyvm            @anyvm               deny
*                         *      @anyvm            @anyvm               deny
*                         *      @anyvm            @host                deny
```

**RPC catalogue (v1)**

| Service | Direction | Payload or limits | Notes |
|---|---|---|---|
| `cloison.Ping` | host→VM | none | health and boot-done |
| `cloison.Exec` | host→VM (VM→VM deny) | argv ≤ 4 KiB, stdio stream | PTY optional; host-terminal risk in 5.8 |
| `cloison.Notify` | VM→host | summary ≤ 64 chars, body ≤ 256, no markup or icons | sanitised, `[domain]` prefix |
| `cloison.Clipboard.Offer` / `.Paste` | VM↔host | text/plain UTF-8 ≤ 1 MiB (v1) | no images in v1 |
| `cloison.FileCopy` | VM→VM via host | tar-like stream; names sanitised | target receives into `~/Incoming/<src>/` |
| `cloison.OpenURL` / `.OpenInDisp` | VM→dispVM | URL ≤ 2 KiB, scheme allowlist | |
| `cloison.secrets.Fetch` | VM→host | returns NM keyfile(s) | `sys-net` only, once per `sys-net` generation |
| `cloison.net.{Status,Scan,Connect,Disconnect}` | host↔sys-net | SSID ≤ 32 bytes, bounded list | drives the Plasma net UI |
| `cloison.usb.{List,Attach,Detach,Eject}` | host↔sys-usb | bounded descriptors | |
| `cloison.Shutdown` | host→VM | none | graceful stop before a hard kill |

**Sanitisation library** (shared crate, fuzzed with `cargo fuzz`):
- UTF-8 only
- strip C0/C1 controls and bidi overrides (U+202A–202E, U+2066–2069)
- NFC normalise
- hard length caps
- refuse NUL and path separators in filenames
- reject `..`, absolute paths and symlinks in file copies (name rules only; the extraction protocol in §5.11 B5 does the rest)
- ships in **M2** so the M3 GUI proxy uses it from day one

### 5.3 VM templates and the image build pipeline

**Template shape.** Each template is an OCI image whose only payload is `/template/`:
- `rootfs.erofs`
- `vmlinux` or `bzImage` plus an optional `initramfs`
- `manifest.json`: name, version, kernel cmdline, sha256 and fs-verity digests, minimum RAM, capabilities such as `gui`, `vfio:wifi`, `vfio:xhci`

**Build** (`cloison/guests/<name>/Containerfile`, Fedora base pinned by digest and bumped by Renovate):
1. `podman build` the guest root filesystem. Guest packages include `cloison-agent`, `waypipe` (GUI templates), and systemd as init in v1; a minimal init is a later option.
2. `podman export`, then `mkfs.erofs` with `SOURCE_DATE_EPOCH`, fixed `-T` and `-U` for reproducibility. Whether `--tar` input is available in Fedora's erofs-utils is *to verify*.
3. Extract the kernel. Cloud Hypervisor direct boot wants `vmlinux` (ELF/PVH); bzImage support in the pinned CH is *to verify*, and the fallback is `extract-vmlinux`.
4. `FROM scratch; COPY template/ /template/`. Push, then cosign sign by digest.
5. CI smoke for templates: `erofs` fsck, manifest schema, and a boot test under CH that reaches the agent `Ping` within N seconds. That needs `/dev/kvm` on the runner (*verify*); otherwise it runs locally through `just cloison-template-test`.

**Distribution**
- **Image-baked templates.** Service templates (`sys-net`, `sys-firewall`, `sys-usb`, guest kernel) are baked into the variant at `/usr/lib/cloison/templates/`. So are `app-base` and `app-browser` **until M6**. They are covered by the host image's identity and by ostree/composefs `/usr` integrity. That identity is the recorded local digest in M1–M2 and the cosign signature from M3. They update atomically with the host. This is the template trust that M2–M5 rely on, and nothing in those milestones pulls a template.
- **Pulled templates (M6 onward):** `app-*` and `dev` with their own cadence, via `cloison template pull`.

**Who does it: a separate trusted installer, not `cloisond`.** `cloisond` parses guest-controlled input and is assumed compromisable (§5.11), so it must not create the records the helper trusts. `cloison template pull <name>@<digest>` goes from the CLI straight to `cloison-template-installer` (§5.1): its own uid `cloison-tinst`, its own unit, polkit admin auth per call, no vsock, no D-Bus path from `cloisond`, and no parser of anything a guest wrote. The request carries only a name and a digest. Repository, key and policy come from the installer's root-owned config in the image. Steps 1–5 run inside the installer. Only the installer uid can write `templates/` and `template-records/`; `cloisond` and the VMMs read them only.

**Pulled-template trust chain (M6; every step must pass, otherwise nothing is installed):**
1. **Signer and repository.** `skopeo copy` with a **Cloison-only** `--policy /usr/share/cloison/containers/policy.json`. Its default is `reject` with no `insecureAcceptAnything` fallback anywhere, and its scope is exactly `ghcr.io/bearyjd/cloison-templates/<name>` with `matchRepository` and the pinned key. It is never run under the shared `/etc/containers/policy.json`, which keeps an unsigned fallback for `docker`.
2. **Digest.** The pull is by digest, resolved from the signed tag. The OCI manifest digest is covered by the signature. `manifest.json` lives **inside** the signed layer, so replacing it changes the digest.
3. **Safe extraction** into `/var/lib/cloison/templates/.staging-<txid>/`. Only the fixed file names (`rootfs.erofs`, `vmlinux`|`bzImage`, `initramfs`, `manifest.json`) are accepted, as regular files. Links, devices and extra entries are refused. The extractor runs with `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)`, `O_EXCL`, and per-file and total size caps.
4. **Payload binding.** The sha256 of each extracted file equals the manifest entry. Then fs-verity is enabled and its digest recorded. Whether fs-verity works on the pinned btrfs is *verify*.
5. **Atomic install.** The staging directory is renamed to `<name>/<digest>/`. The installer then writes the record `template-records/<name>/<digest>.json` (temp file + `rename`), owned by `cloison-tinst`, mode 0444, holding `{oci_digest, signer_key_id, repo, manifest_sha256, file fs-verity digests, version, created}`. Whether an unprivileged uid can enable fs-verity on its own files on the pinned kernel/btrfs is *verify*; if not, the installer runs as root with the same isolation.
6. **Launch check (helper).** The helper accepts a record only if the file and every directory above it are owned by `cloison-tinst` or root, not group/world writable, and the name and digest match the allowlisted template (B1). It then compares the fs-verity digests of kernel, initramfs and rootfs to the record before handing their fds to the VMM. A record failing any check refuses the launch and is logged.

**Template lifecycle policy.**
- **Downgrade:** a pull with a lower manifest `version` than the highest seen for that name is refused unless `--allow-downgrade` is given with polkit admin auth.
- **Revocation:** the variant image ships `/usr/share/cloison/revoked-templates.json` (digests). A revoked digest is refused at launch and offered for deletion.
- **Age:** templates are rebuilt weekly in CI. A launch warns past 30 days and refuses past 90 days, overridable per launch. These numbers are defaults (D12).
- **Provenance:** each template carries an SBOM attestation (`cosign attest`) and an `rpm -qa` manifest. Dependency resolution is not reproducible (Fedora mirrors), so it is recorded rather than claimed reproducible.

**Guest kernels are decoupled from the host kernel pin.** This is a real side benefit: `sys-net` can take newer iwlwifi/iwlmld fixes without moving the i915-sensitive host kernel. `sys-net` needs iwlmld, which per the RUNBOOK means kernel 6.15+ and firmware ≥ v100.

### 5.4 App VMs (disposable and persistent)

- **VMM:** Cloud Hypervisor, direct kernel boot, `console=hvc0`, virtio-blk **read-only** for `rootfs.erofs`. Virtio-blk is preferred over virtio-fs/virtiofsd for templates: no extra daemon and no FUSE protocol surface. **No DAX or virtio-pmem across domains,** because shared page cache is a side channel.
- **Writable layer:** overlayfs with a tmpfs upper (size-capped) for disposables. Persistent domains add a private virtio-blk volume (`/var/lib/cloison/domains/<name>/private.img`, `chattr +C` on btrfs) mounted at `/home` and `/var/lib/containers`.
- **Memory:** virtio-balloon with free-page reporting (CH option *to verify* on the pinned version). **No cross-domain KSM, enforced for the whole run, not only at launch:**
  - The daemon refuses to start a VM while `/sys/kernel/mm/ksm/run` is 1. It also watches the file (inotify plus a 10 s poll). If KSM turns on while VMs run, it refuses new launches, alerts in the tray, and writes `0` through the helper. The owner can change that to "alert only".
  - The daemon audits each VMM process: `/proc/<pid>/ksm_stat` / `ksm_merging_pages` must stay 0 (file availability on the pinned kernel is *verify*). No VMM is started with `PR_SET_MEMORY_MERGE`, and the QEMU backend uses `mem-merge=off`.
  - Whether Bazzite ships ksmtuned or enables process-wide KSM is *UNVERIFIED*. The M0 snapshot shows `run=0` at one moment only.
- **Core scheduling (only if D7 picks it).** The trust group per domain is every task that handles that guest's data: all VMM threads, vhost worker tasks (whether they inherit the cookie on the pinned kernel is *verify*), and that domain's `virtiofsd`, `waypipe` and wlproxy processes. Anything outside the group is treated as cross-domain. SMT side channels stay listed as not defended unless D7 = `nosmt`, whatever M14 does.
- **Fast launch: a pool of pre-booted, never-used VMs (M5).** Each VM is booted fresh, so the kernel RNG, `boot_id`, machine-id and every userspace RNG are freshly initialised. A VM is used once and destroyed. Each gets its own CID, MAC and tap.
  - **Snapshot/restore is CUT in rev 3** (eval §3). A restored snapshot duplicates kernel and userspace RNG state, a known foot-gun, and the pool avoids that for free. The rev 2 restore-barrier design (frozen agent, `Reseed`, two-restore acceptance) is in git history (`7fe10e8`) if this is ever revived.
- **Kata versus Spectrum-style: recommendation (re-tested for `dev` only in the M1.5 spike)**

| Criterion | Kata Containers | Declarative CH domains (Spectrum-style, chosen) |
|---|---|---|
| Fits the Podman workflow | `podman --runtime kata` | OCI used only as the *build format* |
| Host TCB added | containerd/runtime shim plus kata-agent API (large) | `cloisond` with a small RPC; CH |
| GUI / vsock to host | not designed for it; custom plumbing | first-class |
| Default host sharing | virtio-fs share of the container root and mounts | none by default |
| Disposables, snapshot, policy | partial; agent policy exists (Rego) | designed in |

  Use Kata, if at all, only *inside* the `dev` VM or for headless OCI jobs. Spectrum, Ghaf and microvm.nix are the closest prior art for the chosen model; *verify* their current VMM and GUI choices before borrowing.

### 5.5 Network VMs

**Topology (all in netns `cloison`):**

```
BE200 ─VFIO─► sys-net [wlan0 ⇄ eth0] ─tap─ br-net ─tap─ sys-firewall [eth0 | eth1..n]
                                                      ├─tap─ br-app-<dom>  (one bridge per downstream VM, no host IP)
                                                      └─tap─ br-host ─ veth ⇄ root-netns "cloison-up" (host uplink)
```

- **`sys-net`** is a Fedora guest with NetworkManager, wpa_supplicant and iwlwifi/iwlmld, firmware limited to the BE200 files, plus **a copy of `iwlwifi-be200-stability.conf`**. A test `cmp`s it against the host copy, which stays for fallback mode.
  - Stateless: root is erofs plus tmpfs. NM keyfiles arrive over `cloison.secrets.Fetch` into `/run/NetworkManager/system-connections` (NM reads runtime keyfiles there).
  - It NATs for `sys-firewall`.
- **`sys-firewall`** is a tiny guest with nftables. Rules come per downstream domain from the policy through the daemon (allow, deny or allowlist-CIDR, DNS). It does NAT for downstream.
  - **Interim before M8** (the end state while M8 is deferred, rev 3): the same rules as nftables in the `cloison` netns, NATing out of the root netns' Wi-Fi. This is cheaper, but the host kernel parses the traffic, and it is stated as the weaker option.
- **Host uplink:** NM in the root netns manages `cloison-up` as a wired connection whose profile is generated at runtime in `/run`. OpenSnitch/Snitchwatch keep filtering host processes as before. Libvirt's default NAT and Docker keep routing through the host default route.
- **Failure handling and host fallback** (Wi-Fi is the only built-in uplink; there is no Ethernet port). **There is no automatic reclaim.** Once `sys-net` has parsed untrusted traffic, the BE200's firmware and device state must be treated as possibly compromised. Re-binding the host `iwlwifi` to it re-exposes the host driver, and a driver rebind is not firmware sanitisation.
  - **Watchdog (detect only).** If `sys-net` does not report `net.Status=connected-or-idle` within 60 s of start, the watchdog stops the VM. If the VMM does not exit, possibly blocked in a kernel or device operation, it gives up after one attempt and asks for a reboot instead of looping. It leaves the device **bound to `vfio-pci`, unclaimed by the host**, and notifies. It never rebinds `iwlwifi`.
  - **`cloison net mode host` is an explicit security downgrade.** It is owner-run from CLI, tray or TTY, behind a host-styled prompt that says "Wi-Fi will be parsed by the host kernel". It records the choice in `/var/lib/cloison/net-mode` and takes effect at the **next cold boot** (power off, not reboot). At that boot `sys-net` does not start and the host keeps `iwlwifi`. The tray shows a persistent "host Wi-Fi (downgraded)" state until the owner switches back. A runtime `cloison net reclaim --i-accept-downgrade` exists for emergencies: it stops `sys-net`, unbinds vfio-pci, clears `driver_override` and rebinds `iwlwifi`. It carries the same warning and is never invoked by a timer, hook or watchdog.
  - **Independent recovery uplink.** A USB-C Ethernet adapter is the out-of-band path. M9 must prove that the adapter enumerates on a USB controller the **host keeps** (not one given to `sys-usb`, §5.6), with `sys-net` stopped. Which port is safe is *UNVERIFIED*.
- **Captive portals:** open a `disp-browser` behind `sys-firewall` with a portal-only exception rule (Qubes-style).
- **Plasma network UI:** plasma-nm cannot drive NM inside `sys-net`. Proxying NM's D-Bus to the host system bus is rejected as too much surface. Instead the tray/plasmoid uses the narrow `cloison.net.*` RPCs: scan list, connect (PSK entered in a host prompt and stored in the host secret store), disconnect, status. The host secret store is in 5.9.
- **Fallback backend** (if the BE200 fails any §7 gate), called `host-netns`. Choosing it is a recorded security downgrade (D3), not a silent fallback: move the phy into the `cloison` netns (`iw phy <phy> set netns name cloison`) and run NM and wpa_supplicant in a confined container there.
  - This protects the root netns and session from Wi-Fi userspace only.
  - The **host kernel still runs iwlwifi**, so it is clearly weaker and labelled so in the UI.

### 5.6 USB VMs

- **Passthrough backend (M13, CUT in rev 3).** It would hand a whole xHCI to `sys-usb`. M0 (non-root and sudo runs, `docs/research/iommu-groups-p1g7-2026-11-16.md:13-40`) rules both controllers out:
  - `00:0d.0` shares group 11 with the Thunderbolt NHI `00:0d.2`, and serves only two hotplug ports.
  - `00:14.0` shares group 12 with platform SRAM telemetry. It serves the USB-A ports **and** the internal Bluetooth (`8087:0036`, inferred), fingerprint reader and camera.
  - Neither exposes a `reset_method`, so §7.2 step 4a rejects both. The USBGuard fallback is the end state (D4).
  - Independently: set Thunderbolt security so PCIe tunnels are never auto-authorised on the host (boltd).
- **Fallback backend.** Host USBGuard runs default-deny for new devices. Present internal devices are allowlisted by `id` plus `via-port` plus a hash, and HID and mass storage need approval.
  - An approved device goes to its target by `usb-host` redirect: QEMU backend, or attach-at-start for CH, which lacks USB hotplug (*verify*).
  - The host kernel still enumerates descriptors. That is weaker and stated so.
  - Lockout risk: the internal keyboard is assumed to be i8042 and the touchpad I2C-HID, so neither depends on USB policy (*verify keyboard*). The camera, fingerprint reader and Bluetooth are USB (M0), so an enforcing policy must allowlist them first. **Rev 3 (M12a):** the daily image ships `usbguard` installed and **disabled**, with an allow+log config (`ImplicitPolicyTarget=allow`, empty rules, `AuditBackend=FileAudit`). There is no dry-run mode. The owner enables logging (`ujust usbguard-audit`, proposed in [dec 2]), and enforcement waits for M12b and a week of logs.
- **Storage.** Each storage device goes to a **throwaway `usb-disp-N`**:
  - Passthrough mode: `sys-usb` exports the raw block device read-only as an NBD server over vsock, `cloisond` relays it to a fresh disposable, and that VM's kernel parses the filesystem. Write needs explicit approval.
  - Fallback mode: redirect the device straight to the disposable.
  - Copy-out is `cloison.FileCopy`, an `ask` call with size and count caps. The disposable is destroyed on eject.
- **UI:** a tray submenu (M12b) and later a plasmoid listing devices with *host-rendered* names (VID:PID plus the sanitised product string, ≤ 64 chars) and attach targets.

### 5.7 Storage layout

```
/usr/lib/cloison/templates/{sys-net,sys-firewall,sys-usb}/   image-baked, cosign+composefs covered
/usr/lib/cloison/kernels/                                     guest kernels
/usr/share/cloison/{policy.d,domains.d.example,labels.toml}   defaults
/etc/cloison/{domains.d,policy.d,labels.toml}                 declarative owner config (per-deployment /etc, §4.3)
/usr/share/cloison/helper-allowlist.toml, /etc/cloison/helper.d/   root-owned helper allowlist (§5.11 B1)
/var/lib/cloison/credentials/*.cred                           systemd-creds, TPM2-sealed (5.9); in /var so rollback never sees stale secrets
/var/lib/cloison/state-version, cloisond.db, backups/         schema-versioned state (§4.5)
/var/lib/cloison/templates/<name>/<digest>/                   pulled app templates, fs-verity
/var/lib/cloison/domains/<name>/private.img                   persistent volumes (dev)
/run/cloison/<vm>/                                            sockets, tmpfs overlays, pidfiles
```

**Domain definition example**

```toml
# /etc/cloison/domains.d/disp-browser.toml
[domain]
name = "disp-browser"
class = "disposable"            # disposable | persistent | service
label = "red"                   # colour + title prefix
template = "app-browser"        # or "app-browser@sha256:..." to pin
vmm = "cloud-hypervisor"
tags = ["app", "gui", "disposable"]
[resources]
vcpus = 2
memory_mib = 3072
balloon_free_page_reporting = true
[net]
upstream = "sys-firewall"
firewall = "internet"           # named ruleset
[gui]
enabled = true
rendering = "software"
allow_fullscreen = false

# /usr/share/cloison/domains.d/sys-net.toml (excerpt)
[devices.pci.wifi]
match = { class = "0x028000", driver = "iwlwifi" }   # resolved to a BDF at runtime, never hard-coded
backend = ["vfio", "host-netns"]                    # tried in order
```

### 5.8 GUI and KWin integration

**Pipeline:** guest app (Wayland, `wl_shm`, llvmpipe) → `waypipe server` → vsock → per-domain sandboxed `waypipe client` → **`cloison-wlproxy@<dom>`** → KWin. The sandbox for both host-side processes is specified in §5.11 B6 and is an M3 gate.

- Waypipe vsock support and its Rust rewrite version are *to verify* in Fedora's package. Use `--no-gpu`.

**`cloison-wlproxy`** is a trusted Rust filter (wayland-rs) and is the Qubes-GUI-daemon equivalent:
- **Global allowlist:**
  - allowed: `wl_compositor`, `wl_subcompositor`, `wl_shm`, `xdg_wm_base`, `wl_seat` (pointer and keyboard), `wl_output`, `wp_viewporter`, `wp_fractional_scale`, `xdg_decoration` (forced server-side)
  - denied: `wl_data_device` and primary selection (the clipboard), layer-shell, virtual keyboard/pointer, screencopy/data-control, keyboard-shortcuts-inhibit, `xdg_activation` (relayed only with daemon approval), `linux-dmabuf` (software only)
- Rewrites every `set_title` to `[<domain>] <sanitised ≤128 chars>`, and `set_app_id` to `cloison.<domain>.<sanitised>`.
- Fullscreen is denied unless `allow_fullscreen`.
- If KWin supports `wp_security_context_v1` (*verify* for the pinned Plasma), the proxy connects through a security-context listener so KWin itself holds a host-assigned identity.

**Borders and colours:**
- generated KWin window rules (`/etc/xdg/kwinrulesrc` is shared `/etc`, so use a KWin script instead to avoid `/etc` drift): match `cloison.<domain>.*` and force the titlebar colour scheme `Cloison-<label>.colors`
- a KWin script cross-checks `window.pid` against the daemon's map of domain to proxy PID and paints a warning if they don't match
- host-only windows never use `cloison.*` app_ids

**Launchers:**
- `cloison-session` generates `~/.local/share/applications/cloison-<dom>-<app>.desktop` from the template manifest, which is a build-time trusted artifact. Icons come from the manifest; runtime VM icons are not accepted in v1.
- KRunner D-Bus runner (`/usr/share/krunner/dbusplugins/cloison.desktop`; Plasma 6 D-Bus runner support *verify*).

**Clipboard (Qubes model):**
1. Ctrl+Shift+C in a VM window: the **host** issues a copy grant for that focused domain, and only then does the agent's offer get accepted (§5.11 B3).
2. Ctrl+Shift+V on a focused domain: the host issues a paste grant bound to that domain, and the daemon pastes into it.
3. Each transfer is one-shot and shown in a notification that names source and target.
4. Global shortcuts are registered by `cloison-session` (kglobalaccel), so a VM never sees the hotkeys.

**Other GUI rules:**
- Notifications through `org.freedesktop.Notifications`, with the app name `[domain]` and no actions in v1.
- Screenshot and screen-share portals stay host-side; VMs get no portal access.
- **Host-terminal caution:** `cloison exec dev -- bash` in Konsole puts VM-originated escape sequences into a host terminal emulator. It is acceptable for the semi-trusted `dev`. For other domains, use a terminal inside the VM over waypipe (default policy `ask`).

### 5.9 Secrets

- Wi-Fi PSKs and SSH keys for service VMs are `systemd-creds encrypt --with-key=tpm2` blobs in `/var/lib/cloison/credentials/`. TPM2 availability on this laptop is *UNVERIFIED*; the fallback is host-key-only.
- **PCR policy and recovery.** Default: bind to PCR 7 (Secure Boot state) only, never to PCRs that change with every OS upgrade. If unsealing fails after a firmware or Secure Boot change, `cloison creds reseal` re-imports from the owner, using `net import` or a prompt. A host-key-sealed recovery copy exists only if the owner opts in (D12).
- **Key scope.** One credential per (domain, purpose). The helper's root-owned credential map (§5.11 B1) decides which domain may receive which credential. `cloisond` cannot name an arbitrary credential file.
- **No leaks through diagnostics.** The helper and `cloisond` run with `LimitCORE=0` and `systemd-coredump` exclusion. Decrypted material lives only in `memfd`/locked memory and is never logged. The journal gets credential ids, never contents.
- Migration: `cloison net import` reads the existing host NM keyfiles (with sudo) and seals each one.
- **Delivery:** `sys-net` calls `cloison.secrets.Fetch wifi` once per **`sys-net` generation** (§5.11 B2), so a restarted `sys-net` gets a fresh fetch and a restarted attacker cannot fetch twice. The helper decrypts and streams the NM keyfiles. Nothing is written to guest disk; the guest's `/run` is tmpfs.
- SMBIOS type 11 or fw_cfg systemd credentials are an alternative for the QEMU backend. They are not used by default because the owner chose vsock.

### 5.10 Update model

- **Host and service templates:** every update is a new verified-digest switch (§4.3 Operation 2), locally in M1–M2 and from the registry from M3. `bootc upgrade` is a no-op on a digest-pinned deployment and is not used unless D13 picks a signed tracking tag. The parent moves only through `parent.lock`. Rollback and return-to-daily follow the operations in §4.3, with the state rules in §4.5.
- **App templates:** `cloison template pull` (through the installer, §5.3) keeps N=3 digests. Disposables use the newest on next launch; persistent domains pick up a new template on restart (TemplateVM model). Rollback is `cloison template use <name>@<digest>`.
- **Daemon:** ships in the variant image (pinned build), so it updates and rolls back with the host.

### 5.11 Enforceable boundaries (normative; each is a gate for the milestone named, specified before code)

`cloisond` parses guest-controlled input, so it is designed **assuming it gets compromised**. Every rule below holds even then, unless the rule is enforced only by `cloisond` itself, and those rules are marked.

**B1. Helper allowlist (M2).** `cloison-helper` (root) is the only process with device, unit or credential authority.
- **Verb inputs** are `{domain, generation}` and, for USB, a device handle the helper itself issued. Never a path, BDF, unit property, kernel cmdline, credential file or template path.
- **Root-owned allowlist.** The helper resolves everything from root-owned files: `/usr/share/cloison/helper-allowlist.toml` (image) plus `/etc/cloison/helper.d/*.toml`. It refuses to load any file not owned by root with mode `0644` or stricter. The allowlist maps domain → permitted template names, device match rules (vendor:device + topology path, re-resolved and re-checked against the IOMMU group at each call), credential ids, resource caps, and the vsock proof flag per backend (B2). Templates resolve only to image-baked paths under `/usr/lib/cloison/templates/` or to installer-owned records (§5.3 step 6), never to anything `cloisond` wrote.
- **Transient units** come from one fixed template in the helper. `cloisond` cannot pass properties. The VMM runs with `DynamicUser=`, `DeviceAllow=` only for the resolved `/dev/vfio/<group>`, seccomp and `LimitCORE=0`.
- **Interface versioning.** The varlink interface is versioned (`io.github.bearyjd.cloison.Helper1`). Unknown fields are rejected. Helper and daemon ship in the same image, and a version mismatch refuses service.
- **Test (M2):** a fuzzed client on the helper socket. Verbs naming an unlisted domain or device, a path-like string or an extra property are all refused, and the refusal is logged.

**B2. vsock identity and transactions (M2).**
- **Backend proof before use.** For each backend (CH hybrid Unix-socket vsock, or kernel vhost-vsock with QEMU), an M2 test from inside a guest tries three things: connecting to another guest's CID; connecting to every host port 1–65535 except the agent and daemon ports; and spoofing a source CID. Each must fail.
- **Unproven backend ⇒ no vsock device at all (default deny by construction, helper-enforced).** The proof result is a flag in the root-owned image allowlist (B1), bound to the pinned VMM name and version, so a VMM bump clears it until the test is re-run and the image rebuilt. The helper's fixed unit template adds the vsock device (`--vsock` for CH, `vhost-vsock-pci` for QEMU) **only** when the flag is set. Without it the guest has no vsock transport, not merely no RPC. That also means no agent and no GUI, so **M2 acceptance is blocked** until one backend passes; there is no degraded "run without data crossing" mode.
- **Backend rules for new host listeners, including while VMs run.**
  - **CH hybrid vsock (the only v1 backend by default).** A guest connect to port P is terminated by the VMM as a connect to the Unix socket `<vsock-path>_P`. Kernel `AF_VSOCK` listeners on the host are therefore unreachable from the guest, whenever they appear. The per-VM directory holding `<vsock-path>` is created by the helper, owned by root, and writable only by `cloisond`'s uid. The VMM's `DynamicUser` and every other host process cannot add a `_P` socket, so no new listener becomes reachable from a running VM either. The exact path layout on the pinned CH is *verify*.
  - **QEMU vhost-vsock** (kernel transport: any host process binding `VMADDR_CID_ANY` is reachable from every guest, and netfilter does not apply). It is allowed only if a host-wide bind restriction is in place and proven: an SELinux `vsock_socket` rule or a BPF-LSM program letting only `cloisond` bind `AF_VSOCK` (*verify* which is enforceable on this kernel and policy). Without it, QEMU domains get no vsock device (rule above).
  - **Tripwire, not the control.** Every 10 s the helper lists host `AF_VSOCK` listeners (`ss --vsock -l`). A non-Cloison listener stops **running** vhost-vsock VMs (not just new launches) and refuses new ones. An owner acknowledgement does not re-enable them while that listener exists.
- **Generation-bound identity.** Each launch gets a 128-bit generation id. The CID→generation map is held by the helper. A CID is reused only after the old VMM is reaped and a 30 s quarantine has passed. A connection whose CID has no live generation is dropped.
- **Single-use transaction tokens.** An allowed call mints a token bound to `(service, arg, src generation, dst generation, byte cap)`. It expires in 30 s and is consumed on first use. Data streams must present it as their first frame. Token replay, wrong generation or expiry drops the stream. This rule is enforced by `cloisond`, so a compromised `cloisond` breaks it; that is accepted, see §2.

**B3. Clipboard grants (M4).**
- **Copy:** only the host hotkey (kglobalaccel, never seen by the guest) mints a copy grant. It is bound to the domain generation that **KWin reports as focused** at keypress (*verify* that the focus query cannot be steered by the guest), expires in 2 s and is single use. An `Offer` without a live grant is dropped and counted against a rate limit.
- **Paste:** the hotkey mints a paste grant for the focused target. It expires in 10 s. The buffer is cleared after the paste or after 60 s.
- **Notification:** the host notification shows source → target and the byte count, so a substituted offer is visible.

**B4. Launch and resource quotas (M2 for the mechanism, M4 for VM-initiated launches).**
- VM-initiated `OpenURL`/`OpenInDisp`: at most 2 live disposables per source domain, 6 in total, and 3 per minute. Over quota the call becomes `ask`.
- No launch if host `MemAvailable` minus the requested memory falls below a reserve (default 4 GiB). Each VM gets cgroup `MemoryMax`/`CPUQuota`.
- Defaults are D12.

**B5. Safe file transfer (M4).**
- **Host role.** The host never extracts. It relays a restricted stream and counts bytes and entries in a separate sandboxed process. The stream is a restricted tar subset: regular files and directories only, no hardlinks, symlinks, devices, FIFOs or sockets, no xattrs or ACLs.
- **Target agent rules:**
  - Write into a fresh `~/Incoming/<src>/.partial-<txid>/`.
  - Every open uses `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS)` with `O_CREAT|O_EXCL|O_NOFOLLOW`.
  - Modes are masked to 0644/0755, and setuid/setgid/sticky bits are dropped.
  - Names go through the sanitiser.
  - Byte, file-count, depth and per-file caps are enforced again on the target side.
  - The target checks free space first.
  - The finished directory is renamed into place.
  - An abort deletes the partial directory.
- Archives are **never** auto-expanded.
- In v1 the host is never a `FileCopy` target.

**B6. GUI sandbox (M3; sanitising lands in M2 so it is present at M3).** Each domain gets its own `waypipe` client and `cloison-wlproxy`.
- **waypipe client** may reach only (a) its vsock or Unix stream from the VMM and (b) a private socket in `/run/user/<uid>/cloison/<dom>/` served by that domain's wlproxy. It has **no** path to the real `wayland-0`, the session or system bus, `~`, PipeWire, other domains' directories, or the network. Enforcement: `ProtectHome=tmpfs`, `InaccessiblePaths=` for the runtime dir except its own subdirectory, `PrivateNetwork=`, `RestrictAddressFamilies=AF_UNIX AF_VSOCK`, `SystemCallFilter=@system-service`, `NoNewPrivileges=`, and an empty environment (no `DBUS_SESSION_BUS_ADDRESS`).
- **wlproxy** is the only process holding a connection to KWin. That connection goes through a `wp_security_context_v1` listener where the pinned KWin supports it (*verify*). The proxy applies the protocol allowlist and the title/app_id rewriting from §5.8, using the M2 sanitiser.
- **Unit placement.** Systemd user units cannot apply every one of these properties without user namespaces. Which properties take effect under the user manager on this systemd version is *verify*. If a property cannot apply, the process moves to a system unit with `DynamicUser=` that receives only pre-opened fds.
- **Trusted chrome.** Host prompts are layer-shell overlay surfaces. Guests cannot create these, because layer-shell and fullscreen are denied to them. Prompts show the domain colour and a per-install owner-chosen phrase that guest content cannot know.
- **Test (M3 gate):** run a shell under the exact unit sandbox (`systemd-run` with the same properties) as a stand-in for a compromised `waypipe`. It must fail to connect to `wayland-0`, either bus, `~`, another domain's socket and the network. Spoofed titles, app_ids and fullscreen requests from a guest are rewritten or refused.

## 6. Interaction with the existing repo machinery

- **`docker-libvirt-forwarding` / Docker `FORWARD DROP` / `br_netfilter`:** these never touch the `cloison` netns. Test this explicitly with Docker enabled (M3, M8).
- **OpenSnitch/Snitchwatch:** unchanged for host processes. VM traffic is not in the root netns, so the deny trials and readiness checks are unaffected. `boot-check-cloison.sh` asserts that `snitchwatch-readiness` still passes.
- **Bluetooth resume guard:** unchanged. Bluetooth stays on the host.
- **Wi-Fi backend guard:** in the variant with `sys-net` active, the host has no wlan device. The guard must stay a no-op; add a test.
- **kargs:** no new kargs before M9. VFIO binding happens at runtime, so no boot-time `vfio-pci.ids`. `kargs.d` fragments only add arguments. Dropping the inherited `iommu=pt` (D6) would therefore mean the variant layer **replaces** `/usr/lib/bootc/kargs.d/00-iommu.toml`. That is a hardware-tuning change, and it needs owner instruction, the §7.1 evidence, and a reviewed smoke-contract change (§10.2). SMT changes (D7) need the same.
- **suspend:** the logind delay inhibitor is held by `cloisond`. On `PrepareForSleep(true)` it stops VFIO domains (graceful, 3 s, then kill) and returns the devices to vfio-pci idle, then restarts them on resume. A belt-and-braces hook, `cloison/system_files/usr/lib/systemd/system-sleep/bazzite-tower-cloison-vfio-guard`, follows the existing bluetooth-guard pattern: bash, `pre`/`post`, never fatal, logs through `logger`. `InhibitDelayMaxSec` (default 5 s) may need a logind drop-in; measure first.

## 7. Hardware discovery (M0) and hardware acceptance (M9/M13)

**Script:** `scripts/cloison-hw-report.sh` (merged with PR #200; read-only, no root needed, `sudo` adds the kernel log). The variant will also ship it as `/usr/libexec/bazzite-tower-iommu-report` with `ujust iommu-report`. Rev 0 embedded the script here; the repo copy is now the source of truth.

**M0 is topology, not acceptance.** `docs/research/iommu-groups-p1g7-2026-11-16.md` is a **preliminary, non-root** snapshot. It covers groups, endpoints and `reset_method` strings. It cannot answer any step that needs the kernel log or a device test, so it decides neither D3 nor D4. Hardware acceptance happens per device inside M9 (H-net) and M13 (H-usb), and only after the owner approves touching device bindings.

### 7.1 Evidence still required (none of it is in the M0 note)

| Evidence | How | Needed for |
|---|---|---|
| Interrupt remapping enabled (and x2APIC mode) | `sudo journalctl -k -b0 \| grep -E 'DMAR-IR\|IRQ remapping'` | step 1, any VFIO |
| Effective DMA domain type per group | `cat /sys/kernel/iommu_groups/*/type` (`identity` / `DMA` / `DMA-FQ`) under the current kargs | D6 (translation) |
| Invalidation mode | `DMA` = strict vs `DMA-FQ` = lazy flush queue, plus any `iommu.strict=` karg | D6 (strictness; separate from translation) |
| Thunderbolt: firmware-created PCIe tunnels at boot, on hotplug and after resume; `boltctl list` policy; `iommu_dma_protection` | `boltctl`, sysfs under `/sys/bus/thunderbolt/devices/`, kernel log, captured across a suspend cycle | D6, D4 |
| D6 regression evidence: iwlwifi (host mode) and NVIDIA under strict translation | a test deployment with the variant-only karg change, the RUNBOOK health checks, and a DMAR fault grep across a week of use | D6 |
| Reset effectiveness and scope (per candidate) | see step 4 below | D3, D4 |
| Recovery uplink port | USB-C Ethernet enumerates on a host-kept controller | M9, D4 |

### 7.2 Decision tree, per target device (BE200 for `sys-net`, each xHCI for `sys-usb`)

```
1. IOMMU active, interrupt remapping ENABLED (kernel log), allow_unsafe_interrupts=N,
   no pcie_acs_override?                                                    no → STOP: no VFIO for any device
2. Device's group has exactly 1 endpoint (bridges don't count)?             yes → 4
3. Every other endpoint is something the same VM may own (not platform
   telemetry, not the TB NHI unless D4 accepts losing it)?                  no  → FALLBACK (recorded downgrade)
4. Reset proven, not assumed. A non-empty reset_method is evidence, not proof:
   a. reset_method lists flr or pm, or bus where the parent bridge has
      no other device below it (secondary bus reset hits every sibling)?    no  → REJECT passthrough → FALLBACK
   b. empirical: 20× (VM uses device under load → VMM killed → reset →
      fresh VM) works every time, config space reads back sane, no DMAR
      faults, no host hang?                                                 no  → REJECT → FALLBACK
   There is no "boot-once" mode: a device that needs suspend restarts (step 6)
   must be resettable, so "never restart" is not a usable state.
5. Bind/unbind soak: 20× vfio-pci ⇄ native driver on a FRESH device (no guest
   traffic yet); 20× VM start/stop; no DMAR faults, no host hang?           no  → FALLBACK
6. Suspend rows for THIS device pass (§10.5: S3/S4/S7 for sys-net; sys-usb rows cut with M13)? no → FALLBACK
7. (Wi-Fi only) BE200 asserts inside the guest recover via in-guest SW reset,
   and a wedged device recovers via VM restart + step-4 reset?             no  → FALLBACK; open upstream/firmware issue
→ PASSTHROUGH accepted for that device; evidence in docs/research/; owner records D3/D4.
```

- **FALLBACK always means a recorded security downgrade.** `host-netns` for Wi-Fi, USBGuard + redirect for USB. The UI labels it, and the owner signs off on it in D3/D4. It never happens silently at runtime.
- **Reclaim is not part of the tree.** Step 5 rebinds the native driver only on a device that has not yet carried guest traffic. After guest use, the host gets the device back only through the explicit downgrade in §5.5.
- **Provisional reading of the M0 note (UNVERIFIED until §7.1).**
  - Step 1 passes per the sudo run: IRQ remapping in x2apic mode, no ACS override, no DMAR faults at boot (`iommu-groups-p1g7-2026-11-16.md:31`).
  - BE200 (group 23, 1 endpoint, `flr bus`) passes steps 1, 2 and 4a on paper. Steps 4b and 5–7 are open, and they need owner approval to touch device bindings.
  - Both xHCIs fail step 3 as they stand, and neither exposes a `reset_method`, so they are rejected at 4a.

Record each acceptance run in `docs/research/iommu-groups-p1g7-<date>.md`, using the same template as the i915 investigation.

## 8. Phased roadmap (rev 3 order; recommendations pending owner approval)

`:latest` is never touched by any Cloison milestone. Every milestone ships only in the **variant**. M12a is the one exception: it is a daily-image change listed here only for ordering. It goes through the normal daily review path, never mentions `cloison` in daily inputs (`tests/test-cloison-isolation.sh`), and ships the unit disabled (`tests/smoke-cloison.sh:66-72` keeps passing). Each milestone's acceptance includes the §4.3 return-to-daily drill (Operation 4) and its own smoke contract (§10.2). Rollback names the operation used. Risk scale: L (low), M (medium), H (high). **Starts when** names the owner answers (§12) that make a milestone startable.

**Gates that cut across milestones:**
- **Template trust:** every milestone uses only image-baked templates (§5.3). Pulled templates are deferred with M6.
- **Hardware:** no passthrough milestone is active in rev 3. If M9 is revived, it opens with H-net (§7). M0 decides nothing.
- **Publishing (D5):** local builds only through M2. From M3, three separate owner authorisations in order (§4.4): (a) publish `trust-test-*` images only; (b) bootstrap + tests against them; (c) publish the real image. The first remote switch follows (c) and the pre-switch digest gate. Every later update is a verified-digest switch (§4.3 Operation 2).
- **Suspend:** suspend rows are split by implemented device (§10.5).

| # | Milestone | Deliverables | Acceptance tests | Rollback | Risk |
|---|---|---|---|---|---|
| **M0** | Topology (**done**: non-root + sudo runs) | `scripts/cloison-hw-report.sh`, `docs/research/iommu-groups-p1g7-2026-11-16.md` | step 1 passes; topology recorded. **Decides nothing** | none needed (read-only) | L |
| **M1** | Variant scaffold, **local only** (**merged #201; local build + `just smoke-cloison` PASS; laptop drill not done**) | as built: `Containerfile.cloison`, `cloison/build.sh` + marker, isolation test + mutations, `tests/smoke-cloison.sh`, `just build-cloison`/`smoke-cloison` (`docs/CODEMAPS/cloison.md`). Packages, `parent.lock` and the RUNBOOK section are not built yet, and move to whichever milestone first needs them. **Starts when:** D19 (parent channel) | on the laptop: Operation 0, local switch (Operation 1), boot, **Operations 4 and 5 drilled**, `tower-health` green both ways | §4.3 Operation 4/5 | L |
| **M1.5** (new) | **No-new-daemon spike**, two weeks, research only (§8.1). **Starts when:** D15 = spike, D16 answered, and #225 decided either way | plain scripts outside the image, run on the **daily** deployment (libvirt, `/dev/kvm` and podman are already there): disposable browser VM via libvirt or Cloud Hypervisor + waypipe over vsock, a netns per VM; `dev` under krun (`crun-krun`) or Kata; `docs/research/cloison-spike-<date>.md` with a go/no-go on `cloisond` | answers to §8.1 Q1–Q7, with measurements; the exit rule applied and recorded | delete the scripts; nothing in any image | L |
| **M2** | Daemon + enforced boundaries + headless disposable (**only if the spike says go**). **Starts when:** M1.5 go, D2 | `cloisond`/`cloison-helper`/CLI v0, **B1 allowlist**, **B2 backend proof + generations + tokens**, B4 quotas, **sanitiser crate + fuzz targets**, `cloison state backup/restore` + schema v1 (§4.5), D-Bus + polkit (§5.1 auth model), policy parser + `policy check`, netns `cloison`, **image-baked** `app-base`, agent `Ping`/`Exec`/`Shutdown` | as rev 2: `cloison run --disp app-base -- uname -a` round-trips; B1 fuzz refusals; B2 guest-to-guest, host-port and spoof attempts fail; with the proof flag unset the VMM has no vsock device; late host listeners stay unreachable; VM→VM `Exec` denied; `/run/cloison` clean after kill; KSM refuse + watch; state round-trip; older daemon read-only on a newer major; **`cloisond` holds no libvirt `manage` grant** | stop/disable `cloisond`; Operation 3 | M |
| **M3** | GUI + interim network (+ earliest publish gate). **Starts when:** M2, D8 (and §4.4 a/b/c only if publishing) | waypipe/vsock, `cloison-wlproxy` with **B6 sandbox** and sanitised titles, KWin colour script, `.desktop` + KRunner, **image-baked** `app-browser`, interim netns NAT + per-domain nftables (D8). Optional, each step separately authorised: §4.4 (a), (b), (c) | as rev 2: B6 compromise-equivalent test; title prefix and border; spoofing rewritten; fullscreen denied; `wl_data_device` unavailable; Docker enabled does not break VM networking; OpenSnitch readiness unchanged; **if publishing:** §4.4 tests pass, pre-switch gate, a second release arrives by Operation 2 | disable `cloison-session`; Operation 3/4 | M |
| **M4** | Explicit data crossing. **Starts when:** M3 | B3 clipboard grants, B5 FileCopy, `OpenURL`/`OpenInDisp` with B4 quotas, `Notify`, trusted prompts | offer without grant dropped; paste to unfocused domain refused; 2 MiB clipboard refused; hardlink/symlink/device/`../` refused and partial dir removed; quota trips to `ask`; fuzzers 10 min clean | policy reverts to deny-all for the new services | M |
| **M5** | Fast launch + memory hygiene (**snapshots CUT**; Kata moved to M1.5). **Starts when:** M2, D7 | **never-used pool**; balloon free-page reporting; KSM/THP audit; scheduling trust group if D7 | launch-to-window time measured (no number promised); pool VMs single-use; `ksm_merging_pages` 0 for every VMM | pool size 0 (cold launch) | L |
| **M6** | Pulled-template pipeline: **DEFERRED** (eval: a second signing, revocation and downgrade pipeline for one user). **Revive when:** app templates need a faster cadence than variant rebuilds, or `/usr` size makes switches slow (§11). **Starts when:** D12, D14, owner release authorisation | §5.3 as written (installer uid, chain, lifecycle policy) | §5.3 refusal matrix (rev 2 text) | `cloison template use <old digest>` | M |
| **M7** | Persistent `dev` VM, **no `cloisond` needed**. **Starts when:** M1.5 picks the runtime (krun, Kata or a libvirt domain), D9; can run before M2 | a `dev` image (OCI for krun/Kata, or a qcow2/erofs for libvirt), a private volume (a file, backed up), a launcher script or unit, a terminal path (Konsole via `ssh` over vsock if D9 allows, else waypipe), migration doc; **distrobox kept**. **No host Docker/libvirt socket inside `dev`** (§2) | one week of parallel use; 2–3 owner repos build/test inside `dev`; `dev` sees no host secrets beyond what it needs; host sockets absent inside `dev` | keep the distrobox; volume is a file | M |
| **M8** | `sys-firewall` VM: **DEFERRED**; interim nftables (§5.5) is the labelled-weaker end state (D8). **Revive when:** the adversary (D16) includes guest-to-host network attacks, or a host netfilter bug reachable from guest traffic is published | §5.5 topology | rev 2 text | domains point back to interim NAT | M |
| **M9** | `sys-net` spike: **DEFERRED**; `host-netns` is the end state (D3). **Revive when:** D16 = hostile network **and** the owner approves touching device bindings. **Starts when:** D3, D6 | §7.1 capture, §7.2 steps 4b–7 for the BE200 | §7.2, or BE200 recorded as FALLBACK | stop `sys-net`; cold boot | **H** |
| **M10** | `sys-net` managed: **DEFERRED**. **Revive when:** M9 passes | §5.5 watchdog, sleep hook, `net mode host` | S1–S4, S7 (§10.5) | `cloison net mode host` | **H** |
| **M11** | Network UI: **DEFERRED**. **Revive when:** M10 | `cloison.net.*` tray | scan/connect; PSK host-side | hide the widget | L |
| **M12a** | USBGuard **logging in the daily image** (daily PR, not a variant milestone). **Starts when:** D17, after the owner's `lsusb -t` | `usbguard` installed, unit **disabled**, allow+log config (§5.6), `ujust usbguard-audit` to opt in, RUNBOOK row | daily smoke: package present, unit disabled; `smoke-cloison` usbguard check still passes; on the laptop with logging on, a week of `FileAudit` logs and no device blocked | `systemctl disable usbguard`; revert the PR | L |
| **M12b** | USB redirect to a VM (fallback = end state, D4). **Starts when:** M4, D4, a week of M12a logs | enforce with the internal devices allowlisted, tray USB menu, `usb-host` redirect, per-storage `usb-disp` | new HID blocked until approved; internal keyboard, camera, fingerprint and Bluetooth unaffected; stick → disposable → `FileCopy` → eject destroys VM; smoke contract changes through a reviewed change | `usbguard` back to allow+log | M |
| **M13** | `sys-usb` passthrough: **CUT** (eval, M0): both xHCIs fail §7.2 steps 3 and 4a (§5.6) | — | — | — | — |
| **M14** | Slim host: **CUT** (eval: a third image contract; D10 already defaulted it out of scope) | — | — | — | — |

### 8.1 M1.5 spike: questions and exit rule

The spike must prove or disprove each of these, with numbers or logs in the research note:
1. **Launch time.** Cold start of a disposable browser VM to its first window, 10 runs, median and p90, for libvirt/QEMU and for Cloud Hypervisor direct boot. No target is promised.
2. **GUI via waypipe.** A browser over vsock: input, resize, HiDPI, CPU cost of software rendering. Can a wrapper script alone set a trusted title prefix and KWin colour, or does that need a host proxy (B6)?
3. **Network isolation via netns.** A tap in a dedicated netns with nftables NAT. Show there is no path to another VM, to host listeners, or to libvirt's `default` network, and that VM networking still works with Docker enabled (§6).
4. **Memory hygiene.** `ksm_merging_pages` stays 0. Does free-page reporting work on the chosen VMM? Measured RAM per disposable and for `dev` (closes the §11 RAM row).
5. **Polkit/libvirt auth model.** Which step needs which privilege:
   - `qemu:///system` as the desktop user (under #225: the cached prompt; is it acceptable at spike frequency?);
   - `qemu:///session` (no polkit; does user-mode networking such as passt suffice?);
   - CH run as the user (needs only `kvm`).
   Record whether #225 was applied during the measurements.
6. **`dev` under krun or Kata.** One owner repo builds and tests. What is shared with the host by default (virtio-fs of the container root, sockets)? Is that acceptable for the D16 adversary?
7. **What scripts cannot do.** For each of per-call policy, clipboard grants, sanitised titles, file crossing and quotas: met by scripts plus systemd units, met with a gap (stated), or needs a daemon.

**Exit rule.** Build `cloisond` (M2) only if Q7 lists a requirement that matters for the D16 adversary and that scripts plus libvirt/CH plus systemd units cannot meet at acceptable cost. Otherwise:
- re-scope M2–M4 to the missing pieces only;
- ship the scripts in the variant as `cloison/` libexec helpers, each behind a reviewed smoke contract;
- start M7 on the runtime the spike picked.

The spike is time-boxed to two weeks. If it overruns, stop and report what is known. It never touches device bindings, kargs or any image.

## 9. Repo layout changes (proposed)

```
Containerfile.cloison                          # stacked on the daily image by digest (§4.1)
cloison/
  build.sh                                     # runs cloison/build.d/*.sh in order (mirrors build_files/build.sh)
  build.d/10-cloison-packages.sh               # cloud-hypervisor, erofs-utils, waypipe, virtiofsd, usbguard (disabled)
  build.d/20-cloison-sysusers.sh               # sysusers + orphan-shadow pattern from 40-sysusers-fixup.sh
  build.d/30-cloison-units.sh                  # enable cloisond.socket, cloison-netns.service (not sys-net until M10)
  pins.json + verify-pins.py                   # only if D2 = (a) source pins; (c) vendors cloison/daemon/ instead
  parent.lock                                  # recorded verified parent digest + source commit + profile (§4.1)
  bootstrap-trust.sh                           # owner-run §4.4 trust bootstrap + negative tests (never run by image/CI)
  daemon-build.sh                              # offline unprivileged cargo build stage
  guests/{app-base,app-browser,dev,sys-net,sys-firewall,sys-usb}/Containerfile
  guests/sys-net/etc/modprobe.d/iwlwifi-be200-stability.conf   # cmp-identical to system_files copy
  system_files/usr/lib/systemd/system/{cloisond.service,cloisond.socket,cloison-helper.socket,cloison-helper@.service,cloison-vm@.service,cloison-netns.service}
  system_files/usr/lib/systemd/system/cloison-template-installer.{socket,service}   # M6; own uid cloison-tinst (§5.3)
  system_files/usr/lib/systemd/user/cloison-session.service, cloison-wlproxy@.service, cloison-waypipe@.service
  system_files/usr/lib/systemd/system-sleep/bazzite-tower-cloison-vfio-guard
  system_files/usr/lib/sysusers.d/cloison.conf
  system_files/usr/share/dbus-1/system.d/io.github.bearyjd.Cloison1.conf
  system_files/usr/share/polkit-1/actions/io.github.bearyjd.cloison.policy
  system_files/usr/share/cloison/{policy.d/50-default.policy,domains.d/*.toml,labels.toml,colors/*.colors,helper-allowlist.toml,containers/policy.json,revoked-templates.json}
  system_files/usr/share/kwin/scripts/cloison-borders/{metadata.json,contents/code/main.js}
  system_files/usr/share/krunner/dbusplugins/cloison.desktop
  system_files/usr/share/ublue-os/just/61-cloison.just      # iommu-report, cloison-status, net-mode
  system_files/usr/libexec/bazzite-tower-iommu-report        # = scripts/cloison-hw-report.sh
scripts/cloison-hw-report.sh                   # M0 (only file that touches nothing of the daily image)
.github/workflows/build.yml                    # + `cloison` leg ONLY from M3 (D5, owner release authorisation), or a separate build-cloison.yml
.github/workflows/guest-templates.yml          # M6
tests/smoke-cloison.sh  tests/cloison-contracts/M<n>.list  tests/boot-check-cloison.sh  tests/test-cloison-isolation.sh
tests/test-cloison-policy.py (+ tests/fixtures/cloison-policy/)  tests/test-cloison-be200-conf-sync.sh
tests/vm/{disp-roundtrip,helper-fuzz,vsock-routes,gui-sandbox,filecopy,net-downgrade,return-to-daily,suspend-matrix}.sh   # run on the laptop
docs/prp/cloison-kvm-compartments.md  docs/research/iommu-groups-p1g7-<date>.md
docs/CODEMAPS/cloison.md (+ updates to architecture.md, ci-cd.md, image-build.md)   docs/RUNBOOK.md "Cloison variant"
Justfile: build-cloison, smoke-cloison, cloison-template-build, cloison-template-test  (+ CLAUDE.md table, CONTRIBUTING AUTO-GENERATED block)
renovate.json: guest Fedora digests, cloud-hypervisor pin
```

## 10. Testing strategy

1. **Static (every PR):**
   - shellcheck on the new `*.sh`
   - `just check`, plus hadolint on `Containerfile.cloison`
   - `cloison policy check` over the shipped policies
   - policy-parser fixtures
   - the isolation test (daily inputs unchanged)
   - the BE200 conf `cmp` test
   - pins verifier
2. **(§10.2) Smoke: milestone-specific contracts.** `tests/smoke.sh` always runs unchanged, and the variant must pass **every** daily assertion. Exception: M14 runs under its own approved contract. Then `tests/smoke-cloison.sh` reads `/usr/share/cloison/milestone` (baked at build) and applies **that milestone's contract**. Contracts live in `tests/cloison-contracts/M<n>.list`. A static CI check (smoke-contract lint) makes sure every assertion dropped or inverted between M<n-1> and M<n> carries an `intent-change:` line that names the milestone and the plan section. Such a change is reviewed like any assertion edit (repo hard rule: no silent weakening).

   | Assertion | M1–M2 | Changes at | Becomes |
   |---|---|---|---|
   | parent labels = `parent.lock` | yes | never | |
   | binaries present, `cloison` user resolvable | M2+ (binaries), M1 (user) | | |
   | `sys-net` unit disabled | yes | M10 | enabled, with `net-mode` honoured |
   | usbguard **not enabled** (M12a may install it disabled in daily) | yes | M12b | enabled (enforce) |
   | no Cloison kargs; no `vfio-pci.ids` binding | yes | M9+ only if D6/D7 decided | the reviewed karg set, exactly |
   | inherited `00-iommu.toml` unchanged | yes | only if D6 = drop `iommu=pt` | variant replacement present, content exact |
   | image-baked templates present + manifests valid | M2+ | M6 | baked = service templates only |
   | helper allowlist root-owned 0644; no world-writable `/usr/share/cloison` | M2+ | never | |
   | KWin script and KRunner plugin present | M3+ | never | |

3. **Boot test:** the `boot-test.yml` harness (local `podman run --systemd=always` until M3) plus `boot-check-cloison.sh`: daemon reachable, D-Bus name owned, netns created, policy loads, no VFIO binding at boot, `snitchwatch`/`tower-health` unchanged. Under nested KVM (if available), boot `app-base` and `Ping` it.
4. **VM-level tests (laptop, scripted, results in `docs/research/`):** disposable round-trip, B1 helper fuzz, B2 route/spoof attempts, B6 compromise-equivalent sandbox test, clipboard grants, B5 extraction refusals, launch quotas, pool single-use (and the restore barrier if snapshots are enabled), template refusal matrix (§5.3), detect-only watchdog, `net mode host` downgrade, USBGuard approval, §4.3 Operations 4/5.
5. **(§10.5) Suspend/resume matrix, split by implemented device.** Each row runs 10 cycles: 3 short (≤ 2 min), 5 medium (≈ 30 min), 2 long (≥ 3 h, because the Bluetooth stall gets worse with sleep length). Both lid-close and `systemctl suspend`. AC and battery. A row is only a gate for the milestone that implements its devices.

| Row | Running | Gate for | Expect |
|---|---|---|---|
| S1 | nothing (variant idle) | M1 | identical to daily baseline: no i915 `flip_done` storm, Bluetooth guard OK |
| S2 | app VMs only (no VFIO) | M2/M3 | VMs resume, guest clocks resync, GUI reconnects |
| S3 | `sys-net` (VFIO) | M9/M10 | stopped pre-sleep within the inhibitor window; device stays on vfio-pci; reset + restart; connected ≤ N s; no DMAR faults; `Device error - SW reset` count recorded |
| S4 | `sys-net` + `sys-firewall` + app VMs | M10 | app VMs regain network without restart |
| S7 | failure injection: `sys-net` refuses to stop | M10 | VMM killed once; if it won't die, suspend proceeds with the device left on vfio-pci, and resume asks for a cold boot; **no rebind to host**; host does not hang |

   Evidence per cycle: the `journalctl -k` grep set from the RUNBOOK, `cloison status --json`, and timings.

## 11. Risks and open questions

| Risk or unknown | Impact | Mitigation |
|---|---|---|
| Passthrough risks (apply only if M9 is revived; M13 is cut): groups shared with host devices, unproven BE200 reset, a compromised BE200 handed back to the host, iwlwifi DMAR issues, s2idle + VFIO on the known Meteor Lake resume fragility, a VMM stuck in a device op, losing the only uplink | no or unsafe passthrough; hangs; recovery pain | §7.2 tree with recorded-downgrade fallbacks and no ACS override; no automatic reclaim (§5.5); S3/S4/S7 gate; single kill attempt; USB-C Ethernet on a host-kept controller; GRUB Operation 5 |
| `/etc` per-deployment merge, `/var` shared | daily drift; rollback reads newer state | §4.3 operations, §4.5 schema/backups; the single §4.4 `/etc` exception documented with revert |
| Locally modified `policy.json` stops receiving daily-image updates | silent signature-policy drift | bootstrap script diffs against the image default; D11 moves the scope into the daily image |
| waypipe and the wlproxy are new host attack surface | GUI-path escape | B6 sandbox + compromise-equivalent test, protocol allowlist, fuzzing, software-only buffers |
| `cloisond` compromise | broken B2 tokens and B3 grants | B1 limits it to allowlisted actions; residual risk accepted (§2) |
| CH feature gaps (USB hotplug, virtio-snd, vmgenid, restore with config changes) | features blocked | QEMU backend for those domains; never-used pool |
| Variant image size (app templates stay in `/usr` while M6 is deferred) | slower local switches | firmware subset; image size is M6's revive trigger |
| **(rev 3)** Owner uid has silent root: `docker` group, libvirt `manage` ×2, libvirt-dbus (§2) | every host-side Cloison control is bypassable by session code | PR #225 (open) for the libvirt paths **plus** the host Docker change [dock]; until both land, §2's "do not hold" list applies (D18) |
| **(rev 3)** Even with those closed: root at the next `sudo` | targeted session malware still wins | accepted; matches the §2 desktop TCB; untrusted builds go into `dev` (M7) |
| **(rev 3)** #225's `manage` prompt vs Cloison libvirt use | prompts break automation, or a grant to `cloisond` becomes root-equivalent | only the root helper uses `qemu:///system`; `cloisond` never does (§5.1); the spike records the prompt cost (§8.1 Q5) |
| **(rev 3)** Parent channel and refresh (§4.1) | variant silently on the legacy firewall product; stale kernel and security fixes on Cloison | parent = the laptop's channel digest (D19); rebuild cadence; S1 after a kernel move |
| **(rev 3)** Daemon supply chain (D2) | snitchwatch-style repin toil; toolchain NEVRA pins drift separately from source pins | D2 option (c) vendored source until M4 |
| **(rev 3)** Spike verdict measured on a config the owner plans to change | wrong go/no-go | §8.1 Q5 records whether #225 was applied |
| Fedora package availability and versions of CH, waypipe vsock, erofs `--tar`, KWin security-context, KRunner D-Bus plugins, Plasma 6 plasmoid D-Bus | design adjustments | each marked *verify*; check in M1/M3 before relying on it |
| GitHub runners with `/dev/kvm` | no CI boot test of templates | local `just cloison-template-test` gate |
| RAM budget (not yet measured) | contention | measure `free -g`; rough estimates (UNVERIFIED): `sys-net` 0.5 GiB, `sys-firewall` 0.25 GiB, `sys-usb` 0.5 GiB, disposable browser 2–3 GiB, `dev` 8–16 GiB |

**Open questions:**
- Audio and microphone (PipeWire over vsock, or virtio-snd under QEMU)
- Camera for calls (redirect with approval?)
- Screen sharing into a VM
- Where Tailscale lives (host, `sys-firewall` or `sys-net`)
- CUDA in `dev` (NVIDIA passthrough is out of scope)

## 12. Decisions needed from the owner

"Decidable now" means the plan holds enough evidence to choose safely today. Every row has a recommended **default** that applies until the owner says otherwise.

| D | Question | Decidable now? | Recommended default |
|---|---|---|---|
| **D1** | Variant repository | **Decided 2026-11-16** | separate repo `ghcr.io/bearyjd/bazzite-tower-cloison`, with trust installed by the §4.4 bootstrap |
| **D2** | Daemon supply (§5.1 options a/b/c); D-Bus prefix | Open; moot unless the spike says go (D15). Owner answer needed: trust a signed artifact from another repo's CI? ([dec] Q8) | **rev 3 recommends (c) vendored source in this repo until M4**, then reconsider (b) a signed artifact at the first publish; not (a) the snitchwatch pattern. Prefix `io.github.bearyjd` |
| **D3** | BE200: passthrough vs `host-netns` | **Open.** Step 1 passes (sudo M0); still needs 4b, the soak, S3/S4/S7, the recovery port, and D16 | **rev 3 recommends `host-netns` as the end state** (M9–M11 deferred). No passthrough by default |
| **D4** | `sys-usb` xHCI passthrough vs USBGuard redirect | **Open**, but the sudo M0 run settles the facts: both groups hold an endpoint a VM must not own, `00:14.0` also carries camera, fingerprint and Bluetooth, and neither exposes a `reset_method` | **USBGuard fallback as the end state; M13 cut.** Owner confirms |
| **D5** | CI publishing vs local builds | **Decided 2026-11-16** | local builds only until M3. Publishing then needs three separate owner authorisations (§4.4 a/b/c: test images, bootstrap + tests, real image) and the publish-leg workflow PR |
| **D6** | Drop `iommu=pt` (variant only, by replacing `00-iommu.toml`); strict invalidation | **Open.** Owner evidence: `cat /sys/kernel/iommu_groups/*/type`, the invalidation mode, TB tunnel behaviour, a week of iwlwifi/NVIDIA logs | keep `iommu=pt` (inherited); with M9 deferred there is no driver to revisit it |
| **D7** | SMT: `nosmt`, core scheduling for the full trust group, or accept | Open: core scheduling for the full group (vhost workers) and its workload cost are unmeasured | **accept the risk, stated in §2**, until M5 measures the cost; no kargs change |
| **D8** | Interim host-netns firewall vs `sys-firewall` VM | Open: the weaker parser exposure is stated | **rev 3 recommends interim nftables as the end state while M8 is deferred**, recorded as an accepted risk |
| **D9** | Host Konsole may attach to `dev` | Partial: escape-sequence risk is named | allow **`dev` only**; every other domain uses a VM-side terminal over waypipe (`ask`); no credentials forwarded into the host terminal |
| **D10** | M14 slim host in scope; NVIDIA off the host | Moot if the owner accepts the rev 3 M14 cut | **out of scope** (M14 cut) |
| **D11** (new) | Keep the §4.4 local `policy.json` exception, or move the Cloison scope into the daily image's `policy.json` | Yes, at M3 | **move it into the daily image** with a normal reviewed PR at M3, then revert the local edit (retires the only `/etc` exception and its drift) |
| **D12** (new) | Numeric defaults: launch quotas (B4), memory reserve, template age warn/refuse (30/90 days), credential recovery copy | Yes | the values in §5.3, §5.9 and §5.11 as written; no host-key recovery copy unless opted in |
| **D13** (rev 2) | Cloison updates: verified-digest switch per release, or a signed tracking tag with `bootc upgrade` | Yes | **digest switch** (§4.3 Operation 2): the owner picks each digest; a tag lets whoever can push it pick |
| **D14** (rev 2) | Who pushes and signs the `trust-test-*` images, and where | Yes, before M3 | the owner, on the laptop, with the real key for `trust-test-signed` and a throwaway key for `trust-test-otherkey`; no agent uses `cosign.key` |
| **D15** (rev 3) | Run the M1.5 spike first, or go straight to M2 | Yes | **run the spike**; M2 starts only on its "go" |
| **D16** (rev 3) | Cloison's adversary, in priority order: browser/document exploit, hostile repo in `dev`, hostile network | Yes ([eval] Q3) | browser + hostile repo (M2–M4, M7); a hostile network alone is what would revive M8–M11 |
| **D17** (rev 3) | M12a: install `usbguard` in the daily image, disabled, with an allow+log config | After `lsusb -t` and an i8042 keyboard check on the laptop | **yes**, as a separate daily PR ([dec] item 2's second PR); enforcing only in M12b |
| **D18** (rev 3) | Owner-uid root paths: #225 plus leaving `docker` (rootless podman), or accept "session = root" with a written note | Yes ([dock] Q1, Q3: does host `sudo` prompt?) | **both together, or neither on purpose**; §2's "do not hold" list stays until both land |
| **D19** (rev 3) | Cloison parent channel and rebuild cadence | Yes | the digest of the channel the laptop follows (`:snitchwatch-system` today); rebuild within 7 days of a new daily, or at once for a security fix |

**Open questions only the owner can answer (rev 3, max 8):**
1. Who is the adversary (D16)? Is a Wi-Fi-driver exploit in scope at all? If not, M9–M11 stay deferred for good.
2. Is a cached password prompt (~5 min) for read-write libvirt acceptable, including during the spike (#225, [dec] Q1)?
3. Does host `sudo` prompt for a password (no `NOPASSWD`)? Will you move day-to-day Docker to rootless podman and leave the `docker` group (D18)?
4. Do you prefer daemon source in this repo, or a signed artifact from another repo's CI (D2)?
5. Which channel should Cloison stack on, and how many days behind daily may it run (D19)?
6. When can the two-week spike run, and only after the 7.3.4 upgrade and gate C?
7. May `usbguard` ship disabled in the daily image (D17)? Will you paste `lsusb -t` and confirm the internal keyboard is not USB?
8. Is the M1 laptop drill (Operations 0, 1, 4, 5) still wanted before the spike, or after it?

## 13. Prior art

- **Qubes OS:** qrexec (policy v5 format, the control/data split), the GUI daemon (dom0 composition, trusted borders and title prefixes, explicit clipboard), `sys-net`/`sys-firewall`/`sys-usb`, disposables, TemplateVMs.
- **Spectrum OS:** KVM with crosvm/Cloud Hypervisor, declarative VMs, an immutable host, virtio-gpu cross-domain GUI.
- **OpenXT:** Xen with service VMs, measured launch, an input server.
- **Kata Containers:** OCI images as microVMs, agent policy.
- **Also relevant (verify current state before borrowing):** ChromeOS Crostini (crosvm and Sommelier), Ghaf (KVM net-vm and gui-vm), microvm.nix, Firecracker's snapshot-uniqueness guidance.
