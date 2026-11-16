# Cloison: Qubes-style compartments on KVM for bazzite-tower

**Status:** PROPOSAL / design draft, 2026-11-13. Nothing here has been built or tested on the machine yet. "Cloison" (French for a partition or bulkhead) is a working name, and the owner can change it.
**Proposed location:** `docs/prp/cloison-kvm-compartments.md`
**Target:** ThinkPad P1 Gen 7 (Core Ultra 9 185H / Meteor Lake, RTX 4070 Max-Q on the open modules, Intel BE200, s2idle only)
**Decided by owner:** this is a **separate bootc image** (`ghcr.io/bearyjd/bazzite-tower-cloison`, or a `:cloison` tag; see D1), built from this repo. It is not a mode or layer switch inside the daily-driver image. `:latest` stays untouched until the variant is proven.

---

## 0. Executive summary

- **What it is.** An immutable Bazzite host stays the trusted base (the TCB). Untrusted work runs in KVM guests:
  - disposable app microVMs on Cloud Hypervisor
  - a persistent `dev` VM that replaces the `dev` distrobox
  - stateless `sys-net` (BE200 through VFIO), `sys-firewall` and `sys-usb` service VMs

  One Rust host daemon (`cloisond`) owns VM lifecycle, a qrexec-style policy engine, a narrow vsock RPC, and all data that crosses between domains. KDE gets thin front ends: launchers, KRunner, KWin colour rules, a tray/plasmoid, and trusted prompts.
- **How it ships.** It is a second image, `Containerfile.cloison`, built *on top of* the exact daily-driver image from the same commit. So it shares the base pin, all of `build_files/`, `system_files/` and the smoke gate. It adds a variant-only overlay (`cloison/`) and has its own CI leg, smoke, boot-test, cosign signature and tag. You install it with `bootc switch` while the daily deployment is pinned. You return with `bootc rollback` or a switch back to `:latest`.
- **Order of work.** Hardware discovery first, then the variant scaffold, the daemon, disposable app VMs, GUI, data crossing, fast launch, the template pipeline, the `dev` VM, `sys-firewall`, `sys-net`, `sys-usb`, and finally an optional slim host. Every milestone ships on its own, has its own acceptance tests, and rolls back with one command.
- **The first gate is real.** The IOMMU group layout on this machine is **unverified**. M0 runs a read-only script (section 7) whose output decides passthrough versus fallback for Wi-Fi and USB. ACS override is never used.
- **Honest limits.** This does not defend against:
  - host kernel or VMM escapes (that includes the NVIDIA module and the KDE session)
  - side channels
  - device firmware that persists across VM resets (the BE200 Wi-Fi and Bluetooth functions share one card)
  - Meteor Lake s2idle quirks, which force passthrough VMs to be stopped and restarted around suspend

## 1. Goals and non-goals

**Goals**
1. Qubes-like compartments on KVM, with an immutable bootc host and a rollback for every change.
2. Stateless `sys-net` (BE200 through VFIO), `sys-firewall`, and `sys-usb` (controller passthrough where the groups allow it, otherwise per-device redirection with a USBGuard default-deny). Backends are pluggable. Secrets are injected from the host over vsock. USB storage gets a throwaway VM per device, with controlled copy-out.
3. Ephemeral microVMs: direct kernel boot, a read-only erofs template, a tmpfs overlay, snapshot/restore, free-page reporting, and no KSM across domains.
4. KDE integration through one daemon, D-Bus plus CLI, and per-call policy. Clipboard and file copy are explicit, and every string that comes from a VM is sanitised.
5. Gradual migration: the daily driver is never put at risk.

**Non-goals for now**
- GPU acceleration in VMs. Rendering is software only; the NVIDIA and Intel GPUs stay on the host.
- Moving Bluetooth off the host.
- Audio and camera inside VMs. These are open questions (section 13).
- Matching Qubes' Xen-level host minimality in the first phases. The host stays full Bazzite until M14.
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
| A VM pulling data from the clipboard or other domains | No implicit clipboard; every transfer is user-initiated and policy-checked |
| Tampered templates at rest in `/var` | cosign-verified pull, plus fs-verity digests checked at launch |

**Does NOT protect against (stated plainly):**
- **Host kernel bugs reachable from guests:** KVM, vhost-net, vhost-vsock, VFIO and the IOMMU driver.
- **VMM escapes:** Cloud Hypervisor or QEMU device models, virtiofsd, waypipe. A process sandbox only limits the blast radius.
- **The host TCB is large:** the NVIDIA open kernel module, KWin and the whole Plasma session, the Wayland filter proxy, and the daemon's parsers. M14 shrinks this; it never goes away.
- **Side channels:** Spectre-class, cache and timing, and SMT sibling sharing (see D7). Balloon free-page reporting leaks coarse memory-usage patterns. Shared page cache (DAX) is deliberately avoided.
- **Device firmware persistence.** The BE200's runtime state can survive a VM restart if the reset is incomplete. **The BE200's Bluetooth function stays on the host's USB stack.** A Wi-Fi-side firmware compromise could therefore attack the host through the Bluetooth/USB path; isolation of the combo card cannot rule this out. USB device firmware is untouched by a VM reset.
- **`iommu=pt`.** Host-owned devices (the NVIDIA GPU, Thunderbolt, PCH controllers) are identity-mapped, so a malicious host-side device can DMA into host memory. See D6.
- **Compromise of the `dev` VM.** Anything the owner puts in `dev` is exposed if `dev` falls. Persistence is a feature here, not isolation.
- **Traffic analysis, supply chain upstream of signed templates, a malicious owner, physical attacks.**

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
   no guest↔guest vsock; every cross-domain byte is relayed (and policy-checked) by cloisond
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

- **Shared base pin.** The variant never names a Fedora/Bazzite base. Its parent is the daily image built from the same commit:
  - in PRs, the local `verify-safe-pin` tag
  - on main, the `:latest` digest that was just published, verified with `cosign verify` before use

  The rules "never change the base pin without re-reading `docs/research/i915-mtl-resume-2026-06-20.md`" and the weekly `base-bump.yml` therefore cover both images with no second pin to maintain. The variant is built only on the safe-pin parent, not on `:latest-kernel`.
- **Shared versus variant-only content.**
  - All existing `build_files/build.d/*` and `system_files/*` are inherited through the parent, unchanged.
  - Variant content lives only under `cloison/` (its own `build.d/`, `system_files/`, `guests/` and pins), so the daily image's build context and output stay byte-identical.
  - A CI check (`tests/test-cloison-isolation.sh`) asserts that the daily `Containerfile` and `build_files/` contain no reference to `cloison/`.
- **Parent traceability.** The parent digest goes into an OCI label (`io.github.bearyjd.cloison.parent=<digest>`) and into `/usr/share/cloison/parent-digest`. Smoke asserts that the label is present.
- **Why stacked rather than a separate full Containerfile.** It is one pin, one place where the daily customisations are proven, the variant can't drift from the daily image, and the variant diff stays small and reviewable. The M14 slim host is the exception: it gets its own full Containerfile from the same base pin (see M14).

### 4.2 CI path

| Stage | Daily image (unchanged) | Variant |
|---|---|---|
| PR build | `build.yml` legs `safe-pin`, `latest-kernel`, `snitchwatch-system` | New leg `cloison` (or `build-cloison.yml`): build parent locally, then `podman build -f Containerfile.cloison --build-arg PARENT_IMAGE=localhost/...` |
| Smoke | `tests/smoke.sh` | `tests/smoke.sh` (the variant must still pass **every** daily assertion), **then** `tests/smoke-cloison.sh` |
| Boot test | `boot-test.yml` → `tests/boot-check.sh` | Same harness plus `tests/boot-check-cloison.sh` (daemon starts, D-Bus name owned, policy parses, netns created, no VFIO binding at boot) |
| VM-level | n/a | `tests/vm/*.sh` on the laptop (or CI if a KVM runner is available; *verify*) |
| Publish | `release.yml` candidate → cosign sign plus SBOM attestation | Own candidate tag, then cosign sign by digest with the same key, then publish the variant tag. Gated like every release: owner authorisation and the `release` environment approval |

`registries.d/bazzite-tower.yaml` already scopes sigstore attachments to `ghcr.io/bearyjd/bazzite-tower`. If the variant uses its own repository name, add a `registries.d` entry and a `policy.json` scope for it (D1).

### 4.3 Installing it next to the daily driver

```bash
# 1. pin the known-good daily deployment so it is never garbage-collected (verify `ostree admin pin` on this bootc host)
sudo ostree admin pin 0
# 2. switch, signature-enforced
sudo bootc switch --enforce-container-sigpolicy ghcr.io/bearyjd/bazzite-tower-cloison:latest
sudo reboot            # GRUB now lists both deployments
# return path, any time:
sudo bootc rollback && sudo reboot        # or: bootc switch ...bazzite-tower:latest
```

bootc keeps a booted deployment and a rollback deployment, and pinning keeps the daily one listed in GRUB. A second root filesystem install via `bootc install to-filesystem` is possible but not recommended: it is heavy and doubles state.

**`/etc` and `/var` are shared between deployments.** This is the same class of issue as the documented `wifi.backend=iwd` drift. Rules for the variant:
- It writes persistent state **only** under `/etc/cloison/` and `/var/lib/cloison/`. The daily image ignores both.
- Runtime NetworkManager profiles (such as the host uplink veth) go in `/run/NetworkManager/system-connections`, never in `/etc`.
- It never edits shared `/etc` files (NM conf, `containers/policy.json`, logind). USBGuard rules live in `/etc/usbguard/` and are inert in the daily image, because the daily image does not ship usbguard.
- It never leaves VFIO bindings or `driver_override` across a reboot; these are runtime only.
- `tests/boot-check-cloison.sh` plus a manual check on the switch-back path (M1) prove that booting `:latest` after the variant leaves the daily image working.

## 5. Components

### 5.1 Control plane: `cloisond`, `cloison-helper`, `cloison` CLI, `cloison-session`

| Process | Runs as | Responsibilities | Exposure |
|---|---|---|---|
| `cloison-helper` | root, socket-activated, tiny | VFIO bind/unbind (`driver_override`), tap/bridge/veth in the `cloison` netns, starting `cloison-vm@` transient units, fs-verity checks, USB authorize/redirect | varlink socket readable only by the `cloison` group; a fixed verb set with no free-form args |
| `cloisond` | system user `cloison`, seccomp, `ProtectSystem=strict` | domain registry (`domains.d`), policy engine, vsock RPC relay, template store, secrets injection, logind delay inhibitor | system bus `io.github.bearyjd.Cloison1` with polkit actions per method; vsock to guests |
| `cloison` | user | CLI (`cloison ls/start/stop/run/copy/usb/net/template/policy check`) | calls D-Bus |
| `cloison-session` | user service in Plasma | tray (SNI), KRunner D-Bus runner, trusted prompts, notification forwarding, clipboard hotkeys, per-domain `waypipe` client and `cloison-wlproxy` units | session bus only |

- **Language and repo.** Rust, in a separate AGPL-3.0 repo (for example `bearyjd/cloison`). This repo consumes it as a tagged source tarball plus `Cargo.lock`, built in a `cloison-daemon` stage of `Containerfile.cloison`. It follows the snitchwatch pattern: fetch in a prep stage, `--network=none` build as an unprivileged uid, sha256 pins in `cloison/pins.json` checked by a verifier script.
- **Users.** New system users come from `cloison/system_files/usr/lib/sysusers.d/cloison.conf`. Re-run the materialisation the way `40-sysusers-fixup.sh` does (the orphan-shadow workaround), plus a smoke assertion that `getent passwd cloison` resolves.

### 5.2 vsock RPC and policy

**Transport.** Each guest's `cloison-agent` listens on vsock port 5000.

- **Framing:** `u32 BE length ‖ JSON`.
  - Control frames are capped at 64 KiB.
  - JSON is parsed with `deny_unknown_fields`, and an over-length or invalid frame drops the connection.
  - After a call is allowed, the daemon may open a separate **data stream** (raw bytes, byte-counted, capped per policy). This is the qrexec split between control and data.
- **Guests never address each other.** A call names `service`, `+argument` and `target`. The daemon resolves the target, evaluates policy, and (if allowed) opens the stream to the target's agent and splices the two.

**Policy format.** This is Qubes policy v5 style: first match wins and there is an explicit default deny. Image defaults are `/usr/share/cloison/policy.d/*.policy`; owner overrides are `/etc/cloison/policy.d/*.policy`, which sort first. `cloison policy check` validates both (in CI too).

```text
# /usr/share/cloison/policy.d/50-default.policy
# service                 +arg   source            target               action  params
cloison.Notify            *      @tag:gui          @host                allow   rate=10/60s
cloison.Clipboard.Offer   *      @anyvm            @host                allow   max_bytes=1M      # user hit Ctrl+Shift+C in VM window
cloison.Clipboard.Paste   *      @host             @anyvm               allow   max_bytes=1M      # only after host-side Ctrl+Shift+V on focused domain
cloison.FileCopy          *      @tag:usb-storage  @tag:app             ask     max_bytes=4G max_files=10000
cloison.FileCopy          *      @anyvm            @anyvm               ask     max_bytes=4G
cloison.OpenURL           *      @tag:app          @dispvm:disp-browser allow
cloison.OpenInDisp        *      @anyvm            @dispvm              allow
cloison.net.Status        *      sys-net           @host                allow   rate=2/1s
cloison.net.Scan          *      @host             sys-net              allow
cloison.net.Connect       *      @host             sys-net              allow
cloison.secrets.Fetch     wifi   sys-net           @host                allow   once_per_boot=yes
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
| `cloison.secrets.Fetch` | VM→host | returns NM keyfile(s) | `sys-net` only, once per boot |
| `cloison.net.{Status,Scan,Connect,Disconnect}` | host↔sys-net | SSID ≤ 32 bytes, bounded list | drives the Plasma net UI |
| `cloison.usb.{List,Attach,Detach,Eject}` | host↔sys-usb | bounded descriptors | |
| `cloison.Reseed` | host→VM | 64 bytes of entropy | after every snapshot restore |
| `cloison.Shutdown` | host→VM | none | graceful stop before a hard kill |

**Sanitisation library** (shared crate, fuzzed with `cargo fuzz`):
- UTF-8 only
- strip C0/C1 controls and bidi overrides (U+202A–202E, U+2066–2069)
- NFC normalise
- hard length caps
- refuse NUL and path separators in filenames
- reject `..`, absolute paths and symlinks in file copies

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
- **Service templates** (`sys-net`, `sys-firewall`, `sys-usb`, guest kernel) are baked into the variant at `/usr/lib/cloison/templates/`. They are covered by the host image's cosign signature and by ostree/composefs `/usr` integrity, and they update atomically with the host.
- **App templates** (`app-base`, `app-browser`, `dev`) have their own cadence and are pulled with `cloison template pull`. That uses `podman pull` under the existing sigstore `policy.json`, then extracts into `/var/lib/cloison/templates/<name>/<digest>/`, enables fs-verity (btrfs supports it), and records the digest. Each launch re-checks the fs-verity digest against the manifest.

**Guest kernels are decoupled from the host kernel pin.** This is a real side benefit: `sys-net` can take newer iwlwifi/iwlmld fixes without moving the i915-sensitive host kernel. `sys-net` needs iwlmld, which per the RUNBOOK means kernel 6.15+ and firmware ≥ v100.

### 5.4 App VMs (disposable and persistent)

- **VMM:** Cloud Hypervisor, direct kernel boot, `console=hvc0`, virtio-blk **read-only** for `rootfs.erofs`. Virtio-blk is preferred over virtio-fs/virtiofsd for templates: no extra daemon and no FUSE protocol surface. **No DAX or virtio-pmem across domains,** because shared page cache is a side channel.
- **Writable layer:** overlayfs with a tmpfs upper (size-capped) for disposables. Persistent domains add a private virtio-blk volume (`/var/lib/cloison/domains/<name>/private.img`, `chattr +C` on btrfs) mounted at `/home` and `/var/lib/containers`.
- **Memory:** virtio-balloon with free-page reporting (CH option *to verify* on the pinned version). **KSM off:**
  - The daemon refuses to start a VM if `/sys/kernel/mm/ksm/run` is 1 unless the VMM is known not to `MADV_MERGEABLE`.
  - The QEMU backend uses `mem-merge=off`.
  - Check whether Bazzite ships ksmtuned or process-wide KSM (`PR_SET_MEMORY_MERGE`); *verify*.
- **Fast launch:** boot the template to "agent ready, no apps started", snapshot it once per template digest, and restore per launch (CH snapshot/restore). Restoring the same snapshot clones RNG state, so:
  - snapshot **before** any app or crypto library initialises
  - on restore the daemon sends `cloison.Reseed` (written with `RNDADDENTROPY`)
  - use vmgenid if the VMM supports it (*verify* for CH)
  - give each restore its own CID, MAC and tap. Whether CH can restore with a changed device config is *to verify*; if not, keep a small pool of pre-booted, never-used VMs instead.
- **Kata versus Spectrum-style: recommendation and evaluation (M5 spike)**

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
  - **Interim before M8:** the same rules as nftables in the `cloison` netns, NATing out of the root netns' Wi-Fi. This is cheaper but host-kernel parsed, and it is stated as the weaker option.
- **Host uplink:** NM in the root netns manages `cloison-up` as a wired connection whose profile is generated at runtime in `/run`. OpenSnitch/Snitchwatch keep filtering host processes as before. Libvirt's default NAT and Docker keep routing through the host default route.
- **Failsafe and reclaim** (Wi-Fi is the only uplink; there is no Ethernet port):
  - `cloison net reclaim` (also offered by the tray and by a boot-time watchdog) stops `sys-net`, unbinds vfio-pci, clears `driver_override`, rebinds iwlwifi, and restarts host NM.
  - The watchdog fires if `sys-net` hasn't reported `net.Status=connected-or-idle` within 60 s of start.
  - A USB-C Ethernet adapter is the out-of-band recovery path.
- **Captive portals:** open a `disp-browser` behind `sys-firewall` with a portal-only exception rule (Qubes-style).
- **Plasma network UI:** plasma-nm cannot drive NM inside `sys-net`. Proxying NM's D-Bus to the host system bus is rejected as too much surface. Instead the tray/plasmoid uses the narrow `cloison.net.*` RPCs: scan list, connect (PSK entered in a host prompt and stored in the host secret store), disconnect, status. The host secret store is in 5.9.
- **Fallback backend** (if the BE200 group is not isolatable or reset is unreliable), called `host-netns`: move the phy into the `cloison` netns (`iw phy <phy> set netns name cloison`) and run NM and wpa_supplicant in a confined container there.
  - This protects the root netns and session from Wi-Fi userspace only.
  - The **host kernel still runs iwlwifi**, so it is clearly weaker and labelled so in the UI.

### 5.6 USB VMs

- **Passthrough backend.** Hand a whole xHCI to `sys-usb` (stateless, no networking). The candidate is the Thunderbolt-block (TCSS) xHCI; the PCH xHCI keeps internal devices such as the BE200 Bluetooth (`8087:0036`), camera and fingerprint reader. Whether the USB-A ports hang off the TCSS or the PCH controller is **unverified**: the M0 script reports port `connect_type` and `removable`.
  - If the TCSS xHCI shares a group with the Thunderbolt NHI or root ports, then either the whole group goes to `sys-usb` (the host loses Thunderbolt PCIe tunnelling; DP-alt display is driven by i915 and should be unaffected, *verify*), or use the fallback. That is decision D4.
  - Set Thunderbolt security so PCIe tunnels are never auto-authorised on the host (boltd).
- **Fallback backend.** Host USBGuard runs default-deny for new devices. Present internal devices are allowlisted by `id` plus `via-port` plus a hash, and HID and mass storage need approval.
  - An approved device goes to its target by `usb-host` redirect: QEMU backend, or attach-at-start for CH, which lacks USB hotplug (*verify*).
  - The host kernel still enumerates descriptors. That is weaker and stated so.
  - Lockout risk: the internal keyboard is assumed to be i8042 and the touchpad I2C-HID, so neither depends on USB policy (*verify keyboard*). USBGuard first ships in "log, implicit allow" mode for a week before enforcing.
- **Storage.** Each storage device goes to a **throwaway `usb-disp-N`**:
  - Passthrough mode: `sys-usb` exports the raw block device read-only as an NBD server over vsock, `cloisond` relays it to a fresh disposable, and that VM's kernel parses the filesystem. Write needs explicit approval.
  - Fallback mode: redirect the device straight to the disposable.
  - Copy-out is `cloison.FileCopy`, an `ask` call with size and count caps. The disposable is destroyed on eject.
- **UI:** a tray submenu (M12) and later a plasmoid listing devices with *host-rendered* names (VID:PID plus the sanitised product string, ≤ 64 chars) and attach targets.

### 5.7 Storage layout

```
/usr/lib/cloison/templates/{sys-net,sys-firewall,sys-usb}/   image-baked, cosign+composefs covered
/usr/lib/cloison/kernels/                                     guest kernels
/usr/share/cloison/{policy.d,domains.d.example,labels.toml}   defaults
/etc/cloison/{domains.d,policy.d,labels.toml}                 owner config (only shared-/etc footprint)
/etc/cloison/credentials/*.cred                               systemd-creds, TPM2-sealed (5.9)
/var/lib/cloison/templates/<name>/<digest>/                   pulled app templates, fs-verity
/var/lib/cloison/snapshots/<template-digest>/                 restore images (nodatacow)
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

**Pipeline:** guest app (Wayland, `wl_shm`, llvmpipe) → `waypipe server` → vsock → per-domain sandboxed `waypipe client` → **`cloison-wlproxy@<dom>`** → KWin.

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
1. Ctrl+Shift+C in a VM window: the agent offers the VM clipboard to the daemon.
2. Ctrl+Shift+V on a focused domain: the daemon pastes into it.
3. Each transfer is one-shot and shown in a notification.
4. Global shortcuts are registered by `cloison-session` (kglobalaccel).

**Other GUI rules:**
- Notifications through `org.freedesktop.Notifications`, with the app name `[domain]` and no actions in v1.
- Screenshot and screen-share portals stay host-side; VMs get no portal access.
- **Host-terminal caution:** `cloison exec dev -- bash` in Konsole puts VM-originated escape sequences into a host terminal emulator. It is acceptable for the semi-trusted `dev`. For other domains, use a terminal inside the VM over waypipe (default policy `ask`).

### 5.9 Secrets

- Wi-Fi PSKs and SSH keys for service VMs are `systemd-creds encrypt --with-key=tpm2` blobs in `/etc/cloison/credentials/`. TPM2 availability and the PCR policy are *to verify*; the fallback is host-key-only.
- Migration: `cloison net import` reads the existing host NM keyfiles (with sudo) and seals each one.
- **Delivery:** `sys-net` calls `cloison.secrets.Fetch wifi` once per boot. Policy pins it to `sys-net` with `once_per_boot`. The daemon decrypts (through the helper) and streams NM keyfiles. Nothing is written to guest disk; the guest's `/run` is tmpfs.
- SMBIOS type 11 or fw_cfg systemd credentials are an alternative for the QEMU backend. They are not used by default because the owner chose vsock.

### 5.10 Update model

- **Host and service templates:** `bootc upgrade` of the variant tag, which follows the parent through CI. Rollback with `bootc rollback`.
- **App templates:** `cloison template pull` keeps N=3 digests. Disposables use the newest on next launch; persistent domains pick up a new template on restart (TemplateVM model). Rollback is `cloison template use <name>@<digest>`.
- **Daemon:** ships in the variant image (pinned build), so it updates and rolls back with the host.

## 6. Interaction with the existing repo machinery

- **`docker-libvirt-forwarding` / Docker `FORWARD DROP` / `br_netfilter`:** these never touch the `cloison` netns. Test this explicitly with Docker enabled (M3, M8).
- **OpenSnitch/Snitchwatch:** unchanged for host processes. VM traffic is not in the root netns, so the deny trials and readiness checks are unaffected. `boot-check-cloison.sh` asserts that `snitchwatch-readiness` still passes.
- **Bluetooth resume guard:** unchanged. Bluetooth stays on the host.
- **Wi-Fi backend guard:** in the variant with `sys-net` active, the host has no wlan device. The guard must stay a no-op; add a test.
- **kargs:** no new kargs before M9. VFIO binding happens at runtime, so no boot-time `vfio-pci.ids`. `iommu=pt` and SMT changes are D6/D7, which need owner instruction plus on-machine evidence under the hardware-tuning rule.
- **suspend:** the logind delay inhibitor is held by `cloisond`. On `PrepareForSleep(true)` it stops VFIO domains (graceful, 3 s, then kill) and returns the devices to vfio-pci idle, then restarts them on resume. A belt-and-braces hook, `cloison/system_files/usr/lib/systemd/system-sleep/bazzite-tower-cloison-vfio-guard`, follows the existing bluetooth-guard pattern: bash, `pre`/`post`, never fatal, logs through `logger`. `InhibitDelayMaxSec` (default 5 s) may need a logind drop-in; measure first.

## 7. Hardware discovery (M0): IOMMU group script and decision tree

**Script:** `scripts/cloison-hw-report.sh`, also shipped in the variant as `/usr/libexec/bazzite-tower-iommu-report` with `ujust iommu-report`. It is read-only and needs no root; `sudo` adds the kernel log.

```bash
#!/usr/bin/env bash
# cloison-hw-report — read-only IOMMU/VFIO/USB topology report. Changes nothing.
# Usage: bash scripts/cloison-hw-report.sh > iommu-$(date +%F).txt   (sudo for kernel log lines)
set -uo pipefail
shopt -s nullglob

sec() { printf '\n== %s\n' "$1"; }
rd()  { cat "$1" 2>/dev/null || printf '?'; }

sec "kernel / cmdline / firmware"
uname -r
cat /proc/cmdline
rd /sys/class/dmi/id/product_name; echo; rd /sys/class/dmi/id/bios_version; echo
printf 'mem_sleep: %s\n' "$(rd /sys/power/mem_sleep)"

sec "ACS override (must be absent)"
grep -o 'pcie_acs_override=[^ ]*' /proc/cmdline || echo "absent (good)"

sec "IOMMU units"
units=(/sys/class/iommu/*)
if ((${#units[@]})); then printf '%s\n' "${units[@]##*/}"; else echo "NONE: IOMMU not active"; fi
printf 'vfio_iommu_type1.allow_unsafe_interrupts=%s\n' \
  "$(rd /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts)"

sec "kernel log: DMAR / IOMMU / interrupt remapping / iwlwifi faults (needs root)"
journalctl -k -b 0 --no-pager 2>/dev/null \
  | grep -E 'DMAR|IOMMU|[Ii]nterrupt remapping|iwlwifi.*(DMAR|fault|error)' | head -60 \
  || echo "(no access — rerun with sudo)"

sec "Thunderbolt / USB4 DMA protection"
for d in /sys/bus/thunderbolt/devices/domain*; do
  printf '%s iommu_dma_protection=%s security=%s\n' "${d##*/}" \
    "$(rd "$d/iommu_dma_protection")" "$(rd "$d/security")"
done

sec "KSM"
printf 'ksm run=%s pages_shared=%s\n' "$(rd /sys/kernel/mm/ksm/run)" "$(rd /sys/kernel/mm/ksm/pages_shared)"

sec "IOMMU groups (endpoint count excludes PCI bridges, class 0x0604xx)"
for g in $(ls -1 /sys/kernel/iommu_groups 2>/dev/null | sort -n); do
  devs=(/sys/kernel/iommu_groups/"$g"/devices/*)
  endpoints=0
  for dev in "${devs[@]}"; do [[ "$(rd "$dev/class")" == 0x0604* ]] || endpoints=$((endpoints+1)); done
  printf -- '-- group %s (%d device(s), %d endpoint(s))\n' "$g" "${#devs[@]}" "$endpoints"
  for dev in "${devs[@]}"; do
    bdf=${dev##*/}
    drv="(none)"; [[ -e "$dev/driver" ]] && drv=$(basename "$(readlink -f "$dev/driver")")
    printf '   %s class=%s driver=%s reset_method=%s d3cold_allowed=%s\n      %s\n' \
      "$bdf" "$(rd "$dev/class")" "$drv" "$(rd "$dev/reset_method")" "$(rd "$dev/d3cold_allowed")" \
      "$(lspci -nns "$bdf" 2>/dev/null | cut -d' ' -f2-)"
  done
done

sec "USB topology per xHCI (connect_type: hotplug=external port, hardwired=internal)"
for pci in /sys/bus/pci/devices/*; do
  [[ "$(rd "$pci/class")" == 0x0c0330 ]] || continue
  real=$(readlink -f "$pci")
  printf -- '-- xHCI %s (iommu_group %s)\n' "${pci##*/}" "$(basename "$(readlink -f "$pci/iommu_group")")"
  for port in "$real"/usb*/*-0:1.0/usb*-port*; do
    printf '   port %-14s connect_type=%s\n' "${port##*/}" "$(rd "$port/connect_type")"
  done
  for u in /sys/bus/usb/devices/*; do
    [[ -f "$u/idVendor" ]] || continue
    case "$(readlink -f "$u")" in "$real"/*) ;; *) continue ;; esac
    ifcls=$(for i in "$u"/*:*; do rd "$i/bInterfaceClass"; printf ' '; done)
    printf '   dev %-8s %s:%s removable=%s devclass=%s ifclasses=[%s] "%s"\n' "${u##*/}" \
      "$(rd "$u/idVendor")" "$(rd "$u/idProduct")" "$(rd "$u/removable")" \
      "$(rd "$u/bDeviceClass")" "${ifcls% }" "$(rd "$u/product")"
  done
done

sec "Candidates (network 0x0280xx, xHCI 0x0c0330, USB4/TB NHI 0x0c0340, display 0x03xxxx)"
for pci in /sys/bus/pci/devices/*; do
  c=$(rd "$pci/class")
  case "$c" in 0x0280*|0x0c0330|0x0c0340|0x03*) ;; *) continue ;; esac
  g=$(basename "$(readlink -f "$pci/iommu_group" 2>/dev/null)")
  n=0; for d in /sys/kernel/iommu_groups/"$g"/devices/*; do [[ "$(rd "$d/class")" == 0x0604* ]] || n=$((n+1)); done
  printf '%s class=%s group=%s endpoints_in_group=%s reset_method=%s  %s\n' "${pci##*/}" "$c" "$g" "$n" \
    "$(rd "$pci/reset_method")" "$(lspci -s "${pci##*/}" 2>/dev/null | cut -d' ' -f2-)"
done
```

The USB4 NHI class code (`0x0c0340`) and the `reset_method` sysfs file (kernel 5.15+) are as I understand them. If a section comes back empty, cross-check with `lspci -nnk`.

**Decision tree, run per target device (BE200 for `sys-net`, each xHCI for `sys-usb`)**

```
1. IOMMU active, interrupt remapping enabled, no pcie_acs_override?        no → STOP: fix platform first (no VFIO at all)
2. Device's group has exactly 1 endpoint (bridges don't count)?            yes → 4
3. Every other endpoint in the group is something the same VM may own?     yes → passthrough whole group (record what host loses) → 4
                                                                           no  → FALLBACK backend (host-netns for Wi-Fi; USBGuard+redirect for USB)
4. reset_method non-empty (flr / pm / bus)?                                 no  → passthrough only as "boot-once" (start at boot, never restart
                                                                                  without reclaim+host rebind cycle); flag in UI → 5
5. Bind/unbind soak: 20× vfio-pci⇄native driver, 20× VM start/stop,
   device works every time, no DMAR faults, no host hang?                   no  → FALLBACK
6. Suspend matrix rows for this device pass (§10.4)?                        no  → FALLBACK, or passthrough with "stop VM on lid-close
                                                                                  + resume restarts" if only the restart step is flaky
7. (Wi-Fi only) BE200 asserts inside the guest recover via in-guest
   SW reset, and a wedged device recovers via VM restart?                   no  → FALLBACK; open upstream/firmware issue
→ PASSTHROUGH accepted for that device; record evidence in docs/research/
```

Record the outcome in `docs/research/iommu-groups-p1g7-<date>.md`, using the same template as the i915 investigation.

## 8. Phased roadmap

Every milestone after M1 ships only in the **variant** image. `:latest` is never touched. Rollback for any milestone is at least `bootc rollback`, with the daily deployment pinned. Risk scale: L (low), M (medium), H (high).

| # | Milestone | Deliverables | Acceptance tests | Rollback | Risk |
|---|---|---|---|---|---|
| **M0** | Hardware discovery | `scripts/cloison-hw-report.sh`, its shellcheck pass, this PRP, research doc with captured output, decision-tree outcome per device | script runs unprivileged and with sudo on the laptop; output committed; D3, D4 answered | delete the script (it changes nothing) | L |
| **M1** | Variant image scaffold | `Containerfile.cloison` (parent by digest, no packages yet beyond `cloud-hypervisor`, `erofs-utils`, `waypipe`, `virtiofsd`, `usbguard` (installed, disabled), each *verify Fedora availability*), `cloison/build.sh` + `build.d/`, CI leg, `tests/smoke-cloison.sh`, `boot-check-cloison.sh`, isolation test, RUNBOOK install/return section | CI builds both images; variant passes `tests/smoke.sh` + `smoke-cloison.sh`; daily image digest-identical with or without this PR's variant files (build context excludes `cloison/`); on laptop: pin, switch, boot, switch back, `tower-health` green both ways | `bootc rollback` | L |
| **M2** | Daemon skeleton + headless disposable | `cloisond`/`cloison-helper`/CLI v0, D-Bus with polkit, policy parser + `cloison policy check`, `cloison-vm@.service`, netns `cloison`, `app-base` template, agent with `Ping`/`Exec`/`Shutdown` | `cloison run --disp app-base -- uname -a` round-trips; policy denies VM→VM `Exec`; VM killed leaves nothing in `/run/cloison`; KSM check refuses when `run=1`; policy fixture tests in CI | stop/disable `cloisond`; `bootc rollback` | L |
| **M3** | GUI for disposables + interim network | waypipe/vsock, `cloison-wlproxy`, KWin colour script, generated `.desktop` + KRunner, `app-browser` template, interim netns NAT + per-domain nftables | Firefox in `disp-browser` with the `[disp-browser]` prefix and red border; app_id spoofing attempts rewritten; fullscreen denied; `wl_data_device` unavailable in the guest; Docker enabled does not break VM networking; OpenSnitch readiness unchanged | disable `cloison-session`; `bootc rollback` | M |
| **M4** | Explicit data crossing | clipboard hotkeys, `FileCopy`, `OpenURL`/`OpenInDisp`, `Notify`, trusted prompts, sanitiser crate + fuzz targets | bidi/control-char titles neutralised; 2 MiB clipboard refused; copy with `../` names refused; `ask` prompt is host-styled; fuzzers run 10 min clean in CI | policy file reverts to deny-all for the new services | M |
| **M5** | Fast launch + memory hygiene + Kata spike | snapshot/restore pool, `Reseed`, balloon free-page reporting, KSM/THP audit, Kata vs CH spike note | launch-to-window time measured and recorded (target to be set after measuring; no number promised); two restores show different `/proc/sys/kernel/random/boot_id` and RNG output; host RSS returns after guest frees memory | config flag `snapshot=false` | M |
| **M6** | Template pipeline | `cloison/guests/*`, `guest-templates.yml` (build, erofs, cosign, boot-smoke if KVM runner), `cloison template pull/use`, fs-verity checks | unsigned or modified template refused; fs-verity mismatch refuses launch; template rollback by digest works | `cloison template use <old digest>` | M |
| **M7** | Persistent `dev` VM (replacing the `dev` distrobox) | `dev` template (podman, toolchains, Claude Code/Codex inside), private volume, `cloison exec dev` + tmux workflow, migration doc; **distrobox kept intact** | one-week parallel use; build/test of 2–3 of the owner's repos inside `dev`; no secrets left on host that `dev` needs; GUI editor via waypipe usable | keep using the distrobox; the private volume is just a file | M |
| **M8** | `sys-firewall` VM | template, netns topology above, per-domain rules from policy, DNS | app VMs reach the internet only via `sys-firewall`; one app VM cannot reach another; `sys-firewall` restart restores within N s | domains point back to interim netns NAT | M |
| **M9** | `sys-net` spike (manual, no auto-start) | `cloison net try` (runtime VFIO bind of the BE200 → `sys-net` → reclaim), BE200 modprobe copy + `cmp` test | decision-tree steps 4–7 pass; `reclaim` always restores host Wi-Fi; DMAR clean; asserts recover | `cloison net reclaim`; reboot (no persistent binding exists) | **H** |
| **M10** | `sys-net` managed | auto-start, secrets via vsock + `net import`, watchdog reclaim, logind inhibitor + sleep hook, host uplink in `/run` | suspend matrix rows S3–S6 pass; reboot with `sys-net` broken falls back to host Wi-Fi in ≤ 60 s; switching back to `:latest` leaves host Wi-Fi working | `systemctl disable cloison-sys-net`, or `cloison net mode host` | **H** |
| **M11** | Network UI | tray/plasmoid via `cloison.net.*` | scan/connect/disconnect, PSK prompt host-side; no NM D-Bus exposure from the VM | hide the widget; CLI still works | L |
| **M12** | USB policy (fallback first) | USBGuard: one week logging, then enforce; tray USB menu; `usb-host` redirect to a VM; per-storage `usb-disp` | new HID blocked until approved; internal input unaffected; stick → disposable → `FileCopy` → eject destroys VM | `usbguard` disable (`/etc/usbguard` is inert in the daily image) | M |
| **M13** | `sys-usb` controller passthrough (if M0 allows) | VFIO xHCI to `sys-usb`, NBD-over-vsock to `usb-disp`, sleep handling | decision tree passes for that xHCI; dock and USB-C devices work via `sys-usb`; display out unaffected; suspend rows pass | `cloison usb mode fallback` | **H** |
| **M14** | Optional slim host | own `Containerfile.cloison-min` from the **same base pin** (re-read the i915 doc first); evaluate dropping or VFIO-isolating NVIDIA if every display output is confirmed iGPU-driven (*HDMI port wiring unverified*); fewer host services | full smoke + boot test + suspend matrix + one week of daily use | `bootc switch` back to the variant | **H** |

## 9. Repo layout changes (proposed)

```
Containerfile.cloison                          # stacked on the daily image by digest (§4.1)
cloison/
  build.sh                                     # runs cloison/build.d/*.sh in order (mirrors build_files/build.sh)
  build.d/10-cloison-packages.sh               # cloud-hypervisor, erofs-utils, waypipe, virtiofsd, usbguard (disabled)
  build.d/20-cloison-sysusers.sh               # sysusers + orphan-shadow pattern from 40-sysusers-fixup.sh
  build.d/30-cloison-units.sh                  # enable cloisond.socket, cloison-netns.service (not sys-net until M10)
  pins.json + verify-pins.py                   # daemon source/tarball sha256 (snitchwatch pin pattern)
  daemon-build.sh                              # offline unprivileged cargo build stage
  guests/{app-base,app-browser,dev,sys-net,sys-firewall,sys-usb}/Containerfile
  guests/sys-net/etc/modprobe.d/iwlwifi-be200-stability.conf   # cmp-identical to system_files copy
  system_files/usr/lib/systemd/system/{cloisond.service,cloisond.socket,cloison-helper.socket,cloison-helper@.service,cloison-vm@.service,cloison-netns.service}
  system_files/usr/lib/systemd/user/cloison-session.service, cloison-wlproxy@.service, cloison-waypipe@.service
  system_files/usr/lib/systemd/system-sleep/bazzite-tower-cloison-vfio-guard
  system_files/usr/lib/sysusers.d/cloison.conf
  system_files/usr/share/dbus-1/system.d/io.github.bearyjd.Cloison1.conf
  system_files/usr/share/polkit-1/actions/io.github.bearyjd.cloison.policy
  system_files/usr/share/cloison/{policy.d/50-default.policy,domains.d/*.toml,labels.toml,colors/*.colors}
  system_files/usr/share/kwin/scripts/cloison-borders/{metadata.json,contents/code/main.js}
  system_files/usr/share/krunner/dbusplugins/cloison.desktop
  system_files/usr/share/ublue-os/just/61-cloison.just      # iommu-report, cloison-status, net-reclaim
  system_files/usr/libexec/bazzite-tower-iommu-report        # = scripts/cloison-hw-report.sh
scripts/cloison-hw-report.sh                   # M0 (only file that touches nothing of the daily image)
.github/workflows/build.yml                    # + `cloison` leg (PR verify + main release), or a separate build-cloison.yml
.github/workflows/guest-templates.yml          # M6
tests/smoke-cloison.sh  tests/boot-check-cloison.sh  tests/test-cloison-isolation.sh
tests/test-cloison-policy.py (+ tests/fixtures/cloison-policy/)  tests/test-cloison-be200-conf-sync.sh
tests/vm/{disp-roundtrip,gui-proxy,net-reclaim,suspend-matrix}.sh   # run on the laptop
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
2. **Smoke (offline, CI gate for the variant):** `tests/smoke.sh` unchanged, then `smoke-cloison.sh`, which asserts:
   - binaries present
   - `cloison` user resolvable
   - units enabled or disabled as intended (`sys-net` *disabled* until M10)
   - templates present with valid manifests and digests
   - parent-digest label present
   - **no** new kargs, no `vfio-pci.ids` modprobe binding
   - usbguard disabled
   - KWin script and krunner plugin files present

   No existing assertion is weakened.
3. **Boot test:** the `boot-test.yml` harness plus `boot-check-cloison.sh`: daemon reachable, D-Bus name owned, netns created, policy loads, `snitchwatch`/`tower-health` unchanged. Under nested KVM (if available), boot `app-base` and `Ping` it.
4. **VM-level tests (laptop, scripted, results in `docs/research/`):** disposable round-trip, GUI proxy (spoofed title/app_id, fullscreen, clipboard denied), FileCopy limits, snapshot RNG divergence, template signature refusal, net reclaim, USBGuard approval.
5. **Suspend/resume matrix.** Each row runs 10 cycles: 3 short (≤ 2 min), 5 medium (≈ 30 min), 2 long (≥ 3 h, because the Bluetooth stall gets worse with sleep length). Both lid-close and `systemctl suspend`. AC and battery.

| Row | Running | Expect |
|---|---|---|
| S1 | nothing (variant idle) | identical to daily baseline: no i915 `flip_done` storm, Bluetooth guard OK |
| S2 | app VMs only (no VFIO) | VMs resume, guest clocks resync, GUI reconnects |
| S3 | `sys-net` (VFIO) | stopped pre-sleep within the inhibitor window; restarted; connected ≤ N s; no DMAR faults; `Device error - SW reset` count recorded |
| S4 | `sys-net` + `sys-firewall` + app VMs | app VMs regain network without restart |
| S5 | `sys-usb` (VFIO) | devices re-enumerate in `sys-usb`; no host xHCI errors |
| S6 | everything + external display/dock | display path unaffected (i915), dock USB via `sys-usb` |
| S7 | failure injection: `sys-net` refuses to stop | hook or watchdog falls back to reclaim; host does not hang |

   Evidence per cycle: the `journalctl -k` grep set from the RUNBOOK, `cloison status --json`, and timings.

## 11. Risks and open questions

| Risk or unknown | Impact | Mitigation |
|---|---|---|
| IOMMU groups put the BE200 or TCSS xHCI with things the host needs | no passthrough | fallback backends; ACS override never |
| BE200 has no usable reset method, or wedges after VM restart | `sys-net` restart unreliable | "boot-once" mode; reclaim; `host-netns` fallback |
| `intel_iommu` DMAR issues with iwlwifi (README) | Wi-Fi loss | M9 is manual and reversible; DMAR grep in the matrix |
| s2idle plus VFIO, layered on the known Meteor Lake resume fragility | hangs or lost devices | stop/start around sleep, inhibitor plus hook, matrix gate before M10 |
| Losing the only uplink | recovery pain | watchdog reclaim, `cloison net reclaim` from a TTY, USB-C Ethernet |
| Shared `/etc` and `/var` between images | daily image drift | the rules in 4.3, plus a switch-back test every milestone |
| waypipe and the wlproxy are new host attack surface | GUI-path escape | per-domain sandboxed processes, protocol allowlist, fuzzing, software-only buffers |
| CH feature gaps (USB hotplug, virtio-snd, vmgenid, restore with config changes) | features blocked | QEMU backend for those domains; pre-boot pool |
| Variant image size (templates in `/usr`) | slower pulls | firmware subset, only service templates baked in |
| Fedora package availability and versions of CH, waypipe vsock, erofs `--tar`, KWin security-context, KRunner D-Bus plugins, Plasma 6 plasmoid D-Bus | design adjustments | each marked *verify*; check in M1/M3 before relying on it |
| GitHub runners with `/dev/kvm` | no CI boot test of templates | local `just cloison-template-test` gate |
| RAM budget (not yet measured) | contention | measure `free -g`; rough estimates (unverified): `sys-net` 0.5 GiB, `sys-firewall` 0.25 GiB, `sys-usb` 0.5 GiB, disposable browser 2–3 GiB, `dev` 8–16 GiB |

**Open questions:**
- Audio and microphone (PipeWire over vsock, or virtio-snd under QEMU)
- Camera for calls (redirect with approval?)
- Screen sharing into a VM
- Where Tailscale lives (host, `sys-firewall` or `sys-net`)
- CUDA in `dev` (NVIDIA passthrough is out of scope)
- Whether `iommu=pt` stays (D6)

## 12. Decisions needed from the owner

1. **D1:** variant naming: a separate repository `ghcr.io/bearyjd/bazzite-tower-cloison`, or tag `bazzite-tower:cloison` in the existing repository? The latter reuses the `registries.d` scope; the former keeps tag namespaces clean.
2. **D2:** daemon in a separate repo (recommended; AGPL-3.0) or vendored under `cloison/`. Also the D-Bus name prefix (`io.github.bearyjd`).
3. **D3** (after M0): BE200 passthrough versus `host-netns` fallback.
4. **D4** (after M0): `sys-usb` takes the TCSS xHCI (and possibly the TB NHI, losing host Thunderbolt PCIe tunnelling) versus USBGuard redirect only.
5. **D5:** CI publishing of the variant tag (needs release authorisation per `docs/release-playbook.md`) versus local builds only until M3.
6. **D6:** drop `iommu=pt` in the **variant only** (a new variant-only kargs fragment, which needs evidence from this machine that iwlwifi and the NVIDIA GPU survive strict translation) to give DMA protection to host-owned devices.
7. **D7:** SMT policy for the variant: `nosmt` (fewer threads), core scheduling for VMM threads (*verify support*), or accept the risk.
8. **D8:** an interim `sys-firewall` as host-netns nftables (M3–M7), or go straight to the VM.
9. **D9:** whether host Konsole may attach to `dev` (`cloison exec`), or VM-side terminals only.
10. **D10:** the M14 slim host: in scope at all, and whether removing the NVIDIA module from the host is acceptable (it depends on verifying HDMI wiring).

## 13. Prior art

- **Qubes OS:** qrexec (policy v5 format, the control/data split), the GUI daemon (dom0 composition, trusted borders and title prefixes, explicit clipboard), `sys-net`/`sys-firewall`/`sys-usb`, disposables, TemplateVMs.
- **Spectrum OS:** KVM with crosvm/Cloud Hypervisor, declarative VMs, an immutable host, virtio-gpu cross-domain GUI.
- **OpenXT:** Xen with service VMs, measured launch, an input server.
- **Kata Containers:** OCI images as microVMs, agent policy.
- **Also relevant (verify current state before borrowing):** ChromeOS Crostini (crosvm and Sommelier), Ghaf (KVM net-vm and gui-vm), microvm.nix, Firecracker's snapshot-uniqueness guidance.
