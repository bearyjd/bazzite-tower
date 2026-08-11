# Portmaster bootc VM spike

> **Status:** VM gate PASSES (2026-08-06). Portmaster is viable on this image.
> Still **not** approved for the host: interception is unproven, verdict 3
> (Tailscale/NextDNS/TorGuard) is untested, and the VM models none of the
> laptop's real conditions. See "What a PASS does not license" below.

This repository has a deliberately opt-in `portmaster` firewall build variant.
It exists to answer runtime questions that source review cannot settle. It is
not published by CI and its service is disabled in the image, so building it
does not change the `:latest` OpenSnitch image or enable a second firewall.

## What the image packages

- `portmaster-core` from Portmaster `v2.2.1`, exact source commit
  `af0c60140ec4a5d7239aaf61bb3d81ac3c56e51b`, built with `CGO_ENABLED=0`.
- A native systemd unit that runs the core directly—**not** upstream's
  `portmaster-start` installer/bootstrapper. The upstream installer downloads
  mutable modules from `updates.safing.io`, which is incompatible with the
  image pinning requirement.
- Default update settings under `/usr/share/bazzite-tower/`, copied once to
  `/var/lib/portmaster/config.json` immediately before the daemon starts.
  Existing `/var` state is never overwritten.
- A rollback-safe `/usr` mask of OpenSnitch in this variant. The normal image
  does the inverse. Only one firewall can start.

## Build and boot only a disposable VM

```bash
just build-portmaster-spike
just smoke image_name portmaster-spike
just build-qcow2 localhost/image_name portmaster-spike
just run-vm-qcow2
```

Do not run `systemctl enable --now portmaster.service` on the ThinkPad. Inside
the VM, collect a baseline first, then start it manually:

```bash
ss -lunp 'sport = :53'
resolvectl status
sudo systemctl start portmaster.service
systemctl status portmaster.service --no-pager
journalctl -u portmaster.service -b --no-pager
iptables-save
nft list ruleset
getent hosts example.com
```

If networking becomes unusable, stop the service and reboot the VM. Do not
try to repair chains on the host while the failure mode is still unknown.

## Required verdicts

1. **Plain VM:** direct `portmaster-core` starts with the read-only image and
   ordinary public DNS/networking still work.
2. **Resolver conflict:** Portmaster handles the system resolver's port-53
   ownership predictably; no workaround is acceptable yet.
3. **Real-stack VM:** only after the first two pass, authenticate Tailscale and
   reproduce MagicDNS, NextDNS, and TorGuard. MagicDNS, ordinary DNS, and
   NextDNS filtering must all work.
4. **Update pin:** after a restart, verify both false update values remain in
   `/var/lib/portmaster/config.json` and no download tree appears.

Any failure stops the spike. Record raw `ss`, `resolvectl`, journal, and
netfilter captures alongside the verdict rather than attempting a host
workaround.

## VM validation record — 2026-08-05

The final QCOW2 booted successfully and the temporary test unit confirmed that
the Portmaster binary reports `v2.2.1` and guest DNS can resolve `example.com`.
Those observations are **not a service pass**: `systemctl is-active` was read
before the daemon exited.

`portmaster.service` repeatedly exited with `status=2/INVALIDARGUMENT` while
its update module attempted to create
`/var/lib/portmaster/download_binaries`. This happened even though automatic
updates are disabled and the seed helper pre-created that directory.

## Root cause — established from source 2026-08-05

The `download_binaries` path in that journal line is a **red herring emitted by
an upstream message bug**, which is why pre-creating the directory never helped.
Traced against the pinned commit `af0c6014`:

1. The unit passed no `--bin-dir`, so `ServiceConfig.Init()` applied its Linux
   default (`service/config.go`): `BinDir` is the **hardcoded literal
   `/usr/lib/portmaster`**. The install-location-relative derivation
   (`getCurrentBinaryFolder`) is Windows-only, so our real install path
   `/usr/libexec/portmaster` was never consulted.
2. `MakeUpdateConfigs` set the binary updater's `Directory` to that
   nonexistent `/usr/lib/portmaster` (`service/config.go:159`).
3. `updates.New` called `utils.EnsureDirectory(cfg.Directory, 0755)`
   (`service/updates/module.go:196`), which fell through to `os.MkdirAll` and
   hit **EROFS** on the image's read-only `/usr`.
4. The error return one line later prints `cfg.DownloadDirectory` while the
   failure was on `cfg.Directory` (`service/updates/module.go:198`) — hence the
   misleading `download_binaries` text.
5. `service.New` propagated it to `cmds/cmdbase/service.go:81`, whose
   `os.Exit(2)` systemd renders as `status=2/INVALIDARGUMENT`. It is an ordinary
   instance-construction failure, not a panic or an argument-parsing error.

Two further facts fell out of the same trace:

- `Environment=PORTMASTER_DATA=` was **inert** — the daemon reads no
  `PORTMASTER_*` variables. Directories can only be set by flag.
- A missing `index.json` is **not** fatal: `updates.New` falls back to scanning
  the directory and then to an empty index (`module.go:206-233`). The spike does
  not need upstream's artifact bundle.

**Fix applied:** `ExecStart` now pins `--bin-dir /usr/libexec/portmaster
--data-dir /var/lib/portmaster`. `EnsureDirectory` short-circuits on an existing
directory only when its mode already equals `0755`, otherwise it attempts a
chmod that would fail on read-only `/usr` for the same reason — so the build's
`install -d -m 0755` is load-bearing and `tests/smoke.sh` asserts that mode.
The `download_binaries` seeding, which was based on the wrong diagnosis, was
removed.

## The update pin could not have worked either

Reviewing the fix surfaced a second, independent defect. The pin lived in
`/var/lib/portmaster/config.json`, seeded once by a helper that only wrote when
the file was absent. Three facts compound:

- `rpm-ostree rollback` reverts `/usr`, never `/var`. A bad config outlives the
  rollback that was supposed to escape it.
- The seed refused to overwrite an existing file, including an empty one.
- `core/automaticUpdates` **defaults to `true`**
  (`service/core/update_config.go:119`), so an absent, empty, or edited config
  means automatic updates are *on*.

The image therefore had no enforceable pin, only the appearance of one.

**Fix:** the unit now shadows the path with the image's own copy:

```
BindReadOnlyPaths=/usr/share/bazzite-tower/portmaster-config.default.json:/var/lib/portmaster/config.json
```

The pin becomes image content, so it reverts with a rollback like every other
setting. systemd creates the mount destination itself (`systemd.exec`: "the
destination directory must exist or systemd must be able to create it"), so
nothing seeds `/var` and the seed helper is deleted. A missing source fails the
unit **before** `ExecStart` — fail closed, which is the right posture for a
daemon whose filter chain ends in `--mark 0 -j DROP`. Note that this also means
the on-disk `/var` copy can never act as a fallback: a bind-mount setup failure
stops the daemon from running at all, so there is nothing left to read it.

## Running the gate

The four verdicts above are now encoded as assertions in
`tests/portmaster-vm-gate.sh`. Run it **inside the disposable VM only**:

```bash
sudo tests/portmaster-vm-gate.sh; echo "exit=$?"
```

It exits 0 only if every hard check passes. It samples `is-active` continuously
across a 30-second dwell and requires `NRestarts=0` and `Result=success`,
because the 2026-08-05 run recorded a pass from a single reading taken before
the daemon exited. It also proves the config bind mount actually applied by
matching the mountpoint, source, and `ro` flag in the daemon's own
`/proc/PID/mountinfo` — checking the pinned *value* would pass whether or not
the mount worked.

## VM gate verdict — 2026-08-06: PASS

`tests/portmaster-vm-gate.sh` exited 0 with all twenty checks green, against a
qcow2 whose booted unit was verified to contain the code under test.

```
V0   image contains the code under test              ok
V1   holds active 30s, NRestarts=0, Result=success   ok
V4a  mount applied / from /usr / ro / values read    ok
V3   no config-write errors                          ok
V5   journal carries daemon output                   ok
V2   name resolution survives                        ok
V6   chains appear, gone after stop, nothing moved   ok
V6b  cleanup idempotent                              ok
V7   fails closed without the pin source             ok
```

**Both blockers from the parent PRP are answered.**
[parameterized-firewall-module.md](../prp/parameterized-firewall-module.md) §"The
two blockers that actually defer it" named DNS and `/var`-resident config. In a
plain VM the daemon coexists with the guest resolver, and the config now lives
in `/usr`, is bind-mounted read-only into the daemon's namespace, reverts with a
rollback, and is read back through `nsenter` from inside that namespace rather
than from a path that would match either way.

Runtime confirmation from the journal: `running Portmaster 2.2.1 (... from
af0c60140ec4a5d7239aaf61bb3d81ac3c56e51b [clean] ...)`, all managers started,
API listening on `127.0.0.1:817`.

Also settled: `portmaster-core` does **not** write its own config, so the
read-only pin does not fight the daemon. That was an open question source review
could not answer.

### What a PASS does not license

- **Verdict 3 is untested.** Tailscale MagicDNS, NextDNS, and TorGuard need real
  credentials in a VM. A guest resolving `example.com` over user-mode NAT says
  nothing about a four-way contest for the resolver path.
- **Per-process/per-profile rule matching is untested.** V8 (below) proves the
  global `filter/defaultAction` fallback is enforced, not that individual app
  profiles or rules are.
- **The VM models almost none of the real machine.** libvirt, VPN transitions,
  suspend/resume, NetworkManager resolver rewrites, and Docker under load are
  all absent. The ThinkPad has all of them.

Do not enable this variant on the host on the strength of this verdict.

## VM-gate SSH

The gate above required typing/pasting into a GUI QEMU console — brittle
(no host/guest clipboard in this setup) and the reason the 2026-08-06 run's
findings took a full session to extract. Two fixes now exist:

- **`disk_config/vm-test.toml`** — a VM-gate-only bootc-image-builder config
  that adds a `gate` user and an SSH key, unlike `disk_config/disk.toml`
  (what ships, what the ThinkPad boots), which has neither. Never used for a
  published image.
- **`--build-arg VM_GATE_SSH=1`** (`build-portmaster-spike` already passes
  it) — enables `sshd.socket` inside the container image, gated off by
  default so `:latest` is unaffected either way.
- **`just run-vm-ssh`** — boots the disk with a fresh ephemeral keypair and
  an explicit `hostfwd` via plain `qemu-system-x86_64`, printing the exact
  `ssh` command to connect. See the Justfile recipe's own comment for why it
  doesn't reuse `run-vm-qcow2` or `spawn-vm`: both were tried and failed for
  reasons specific to this host, not this repo's config —
  `run-vm-qcow2`'s `qemux/qemu` docker wrapper has a qcow2 GPT-probing bug
  (its bundled `qemu-img dd -f qcow2 -O raw bs=512 skip=N count=M` silently
  truncated output short of the requested size on this host's qemu-img
  10.2.2, independent of anything in this repo), and `spawn-vm`'s
  `systemd-vmspawn --network-user-mode` has no port-forward option at all —
  its documented alternative, `--vsock` + `systemd-ssh-proxy`, was tried and
  failed empirically (`Connection reset by peer` on every attempt; the
  daemon-side `systemd-ssh-generator` never bound to vsock port 22 for a
  reason not root-caused, since diagnosing further needed console access to
  the guest that wasn't available on that boot path either).

One real dead end costed most of the effort here and is worth recording
precisely: an initial `run-vm-ssh` draft using the `*.secboot.` OVMF
firmware (matching what `run-vm-qcow2`/`spawn-vm` use) hung indefinitely —
qemu pegged near 100% CPU, no error visible, looking exactly like a boot
timing issue. It was not one. Attaching `-serial file:...` (now permanent in
the recipe) showed the real cause immediately: `error: bad shim signature`
— a hand-rolled `cp` of the `OVMF_VARS_4M.secboot.qcow2` template is not
pre-enrolled with the shim/kernel trust chain that `systemd-vmspawn`'s own
vars handling sets up automatically, so GRUB refused to boot and sat at a
"Press any key to continue" prompt that `-display none` can never answer —
indistinguishable from a hang without a console. `run-vm-ssh` uses the
plain (non-secboot) OVMF instead; this variant is disposable test tooling,
not a Secure Boot test.

## V8 — interception enforcement (2026-08-10)

Every check above (V0–V7) proves the daemon boots, keeps its config, and
installs/removes netfilter rules — none of them prove a connection is ever
matched to a verdict. A daemon that boots, resolves DNS, and installs empty
chains passes the whole 2026-08-06 gate while intercepting nothing.

V8 closes that gap using `filter/defaultAction` (default: `permit`) — the
one knob that changes a connection's outcome without a profile, a UI, or
credentials, so it's usable headlessly: probe `1.1.1.1:443` under the
pinned config's implicit permit (reachable), force `defaultAction=block` via
the same bind-over-`CONFIG_SRC` technique V7 uses, restart, probe again
(must be blocked), then revert and probe once more (must be reachable
again).

**First run: false FAIL, root-caused and fixed.** The new
`restart_with_config` helper read `systemctl is-active` once right after
`systemctl start` and returned success on that reading — precisely the
class of bug the 2026-08-05 postmortem (above) already burned this gate
script on once, reintroduced in new code. `Type=simple` marks a unit active
the instant `ExecStart` forks, before a near-immediate crash is detected;
the journal showed the daemon crashing within 100–600ms of each of V8's two
restarts, twice, with zero log output and exit code 0, before a later
attempt (V7's, using `/dev/null` as the pin source) finally produced normal
BOF output. Both fake-successful restarts left no daemon running at all,
which is why V6 (immediately after V8 in the script) found zero PORTMASTER
chains and why V8's own block check saw the probe stay reachable — nothing
was enforcing anything either way. Fix: `restart_with_config` now sleeps 3s
after the first `is-active` read and re-verifies before trusting it.

### VM gate verdict — 2026-08-10: PASS (interception confirmed)

Re-run after the fix, same qcow2, same daemon build:

```
V0   image contains the code under test               ok
V1   holds active 30s, NRestarts=0, Result=success    ok
V4a  mount applied / from /usr / ro / values read     ok
V3   no config-write errors                           ok
V5   journal carries daemon output                    ok
V2   name resolution survives                         ok
V8   probe reachable under permit                     ok
V8   daemon restarts under forced block config         ok
V8   probe blocked under defaultAction=block           ok
V8   daemon restarts back under pinned (permit) config  ok
V8   probe reachable again after reverting             ok
V6   chains appear, gone after stop, nothing moved     ok
V6b  cleanup idempotent                                ok
V7   fails closed without the pin source               ok
```

Interception is no longer an open question at the `defaultAction` level:
forcing `block` measurably blocks a real connection, and reverting to the
pinned config measurably restores it, both confirmed via the daemon's own
process lifecycle rather than a `systemctl is-active` snapshot alone. Still
open: per-process/per-profile rule matching (untested beyond the global
default), and Verdict 3 (Tailscale/NextDNS/TorGuard). Same conclusion as
2026-08-06 stands — do not enable this variant on the host on the strength
of this verdict.

### Prior runs that produced no verdict

Two earlier runs on 2026-08-05 have to be discarded, both for measurement faults
rather than daemon behaviour, and both worth knowing about:

1. **A stale image was graded for a full cycle.** `_rootful_load_image` ran
   `podman image scp`, returned 0, and silently declined to move the tag because
   the destination tag already existed. bootc-image-builder consumed the
   three-day-old rootful image. Every freshness guard checked artifact
   *identity* (mtime, size, image ID) at a layer that was correct, while the
   layer that actually fed the disk build was stale. V0 exists because of this.
2. **Six gate checks measured the wrong thing**, of which three could produce a
   false pass: the pinned-value check read `CONFIG_DST` from outside the unit's
   private mount namespace, where it is the empty file systemd creates as the
   mount destination; `journalctl | grep -q` under `set -o pipefail` reported
   SIGPIPE(141) as failure; the rule baseline was captured while Docker was
   still installing chains; and the ruleset comparison diffed the
   `[packets:bytes]` counters that `iptables-save` emits on chain-policy lines,
   which change with every packet and could never match on a live system.
