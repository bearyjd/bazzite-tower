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
updates are disabled and the seed helper pre-created that directory. The
directory/configuration contract for the update module under the hardened
systemd unit must be established before another VM run. Do not merge or enable
this variant on the host until it is stable across a full boot.
