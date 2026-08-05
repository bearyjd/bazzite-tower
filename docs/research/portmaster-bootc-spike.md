# Portmaster bootc VM spike

> **Status:** VM gate failed; not approved for the host or merge.

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

**Still unverified:** every fix here is source-derived. The gate has not been
run. Do not merge or enable this variant on the host until it exits 0, and note
that a pass still says nothing about Docker, libvirt, VPN transitions,
suspend/resume, or NetworkManager resolver rewrites — none of which the VM
models.
