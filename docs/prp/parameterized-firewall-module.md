# PRP — Parameterized application-firewall module

> **Status:** OpenSnitch parameterization remains proposed. A disabled,
> Portmaster-only VM spike implementation now exists; see
> [the runtime runbook](../research/portmaster-bootc-spike.md). Scoped
> deliberately narrower than the original request — see [Scope decision](#scope-decision).
>
> **Phase 1 investigation this rests on:** OpenSnitch surface audit + a source
> read of `safing/portmaster` at `v2.2.1` (`af0c601`). Every upstream claim
> below cites the file and line it came from.

## Problem

`build_files/build.sh` installs and enables OpenSnitch as a hardcoded
assumption. The daemon name, config path, config schema, install method, and
version pin are all inlined at `275–354`, and both test files hardcode
`opensnitch.service`. Swapping the interception engine means editing five
files and re-deriving which assertions were daemon-specific.

The goal is a module where one variable selects the daemon, so a second
implementation is mechanical rather than archaeological.

## Scope decision

**This PRP builds the parameterization with OpenSnitch as the only
implementation. It does not ship a Portmaster variant.**

That is narrower than the original brief, which asked for both variants
buildable and A/B-able. The reason is that Phase 1 surfaced three blockers,
and the one the brief worried about turned out to be the least severe.

### The sourcing problem is solved and was never the real obstacle

`safing/portmaster` publishes **zero release assets** — verified across
`v2.2.1`, `v2.1.19`, `v2.1.18`, `v2.1.7`, `v2.0.25`, all `assets=0`. There is
no pinnable RPM equivalent to OpenSnitch's.

But the daemon does not need one. `cmds/portmaster-core` is a plain Go main,
and the Earthly `+go-build` target reduces to:

```
CGO_ENABLED=0 go build \
  -ldflags="-X github.com/safing/portmaster/base/info.version=${VERSION} \
            -X github.com/safing/portmaster/base/info.buildSource=${SOURCE} \
            -X github.com/safing/portmaster/base/info.buildTime=${BUILD_TIME}" \
  -o /tmp/build/ ./cmds/portmaster-core
```

Earthly is orchestration; Rust/Tauri/Angular exist only for the GUI and the
installer bundle, neither of which this image wants. Sourcing is therefore
"clone at a pinned tag, `go build`, checksum the output" — *more* reproducible
than the OpenSnitch path, which trusts a prebuilt binary behind a hash.

### The two blockers that actually defer it

**1. DNS.** Portmaster is a resolver, not just a firewall that observes DNS.
It binds `localhost:53` by default (`service/nameserver/config.go:15`;
`0.0.0.0:53` under a second branch at `:25`) and ships conflict-detection that
probes for competitors:

```go
// service/nameserver/conflict.go
var commonResolverIPs = []net.IP{
	net.IPv4zero,
	net.IPv4(127, 0, 0, 1),  // default
	net.IPv4(127, 0, 0, 53), // some resolvers on Linux
	net.IPv6zero,
	net.IPv6loopback,
}
```

The `127.0.0.53` entry is systemd-resolved — upstream carries this code because
the collision is routine. This host runs Tailscale (with MagicDNS), NextDNS,
and TorGuard. That is a four-way contest for the resolver path on a daily
driver. The unit also declares `Conflicts=firewalld.service`.

**2. Configuration lives in `/var`, not `/etc`.** `base/config/main.go:40`
resolves `config.json` under `DataDir` → `/var/lib/portmaster/config.json`.
Nothing Portmaster reads is image-managed. Every setting that matters —
including `core/automaticUpdates=false`, which is how the version pin is
enforced — must be seeded into `/var` by a first-boot oneshot. That is a
different architecture from every other configurable in this repo, and it
collides directly with the rollback-hygiene requirement below: `/var` is
precisely what `rpm-ostree rollback` does not clean.

### Why parameterize at all with one implementation

A parameterized module with a single implementation is speculative generality,
and that is a fair objection. It survives because four of the original
requirements pay off with OpenSnitch alone:

| Requirement | Value with one daemon |
|---|---|
| systemd preset instead of `systemctl enable` | Real now — removes an `/etc` symlink that survives rollback |
| Rollback-cleanup script | Real now — OpenSnitch leaves `/etc/opensnitchd/rules/` behind today |
| Event-seam documentation | Real now — Snitchwatch attaches to the gRPC seam |
| Single selector variable | Cheap; makes a second variant mechanical |

Masking and A/B testing only pay off with two daemons. The masking *seam* is
built anyway because it is a few lines and its absence is what makes adding
variant two dangerous.

## Design

### Selector

A build arg, mirroring how `BASE_IMAGE` already selects a variant in
`Containerfile:9`:

```dockerfile
ARG FIREWALL_DAEMON=opensnitch
```

Threaded to `build_files/build.sh` as an environment variable. Valid values:
`opensnitch` (implemented), `portmaster` (reserved, hard-fails today).

Validation at the top of the module — fail the build loudly on an unknown
value rather than silently shipping no firewall:

```bash
case "${FIREWALL_DAEMON:-opensnitch}" in
    opensnitch) ;;
    portmaster)
        echo "FIREWALL_DAEMON=portmaster is reserved but not implemented." >&2
        echo "See docs/prp/portmaster-viability-spike.md — three questions" >&2
        echo "must be answered in a VM before this variant is safe." >&2
        exit 1
        ;;
    *)
        echo "Unknown FIREWALL_DAEMON='${FIREWALL_DAEMON}'." >&2
        exit 1
        ;;
esac
```

Reserving the value and failing is deliberate: a typo'd selector must not fall
through to "no firewall installed," which would be a silent security
regression.

### File layout

Extract the firewall concern out of `build.sh`, which is already 356 lines
covering unrelated subsystems:

```
build_files/
  firewall/
    install.sh              # dispatch on FIREWALL_DAEMON
    opensnitch.sh           # everything OpenSnitch-specific
    portmaster.sh           # reserved; currently just the hard-fail
system_files/
  usr/lib/systemd/system-preset/
    80-bazzite-tower-firewall.preset   # generated per variant at build time
```

This follows the repo's existing "one concern per file" rule for
`system_files/`, and gives the eventual second variant a file to live in
rather than a branch inside a 350-line script.

### Enablement via preset

Replace `systemctl enable opensnitch.service` (`build.sh:353`) with a preset
fragment plus `systemctl preset`:

```
# system_files/usr/lib/systemd/system-preset/80-bazzite-tower-firewall.preset
enable opensnitch.service
disable portmaster.service
```

```bash
systemctl preset opensnitch.service
```

The win is that preset state lives in `/usr` and travels with the image, so a
rollback reverts it. `systemctl enable` writes
`/etc/systemd/system/multi-user.target.wants/opensnitch.service`, which
persists across `rpm-ostree rollback` and would leave a rolled-back image
starting a daemon its own build never enabled.

**Scope note worth an explicit decision:** `build.sh` calls `systemctl enable`
**16 times** (`157–171`, `199`, `208`, `215`, `225`, `237`, `256`, `265–266`,
`353`). The rollback argument applies to all of them, not just the firewall.
Converting only the firewall leaves the repo internally inconsistent. Two
defensible options — decide before implementing:

- **A.** Convert only the firewall now; open a follow-up for the other 15.
  Keeps this change reviewable.
- **B.** Convert all 16 in one pass. Consistent, but mixes an unrelated
  systemd-hygiene refactor into a firewall change and touches every subsystem
  the smoke gate covers.

Recommendation: **A**, with the follow-up filed immediately so the
inconsistency is tracked rather than forgotten.

### Masking the non-selected daemon

The brief named "two daemons contending for nfqueue on first boot" as the
primary failure mode. **Phase 1 found that framing is wrong, and the real
mechanism is worse.**

They do not share a queue number, and they do not share a firewall subsystem:

| | OpenSnitch 1.8.0 | Portmaster 2.2.1 |
|---|---|---|
| Backend | **nftables**, family `inet`, table `opensnitch`, chains `filter_output` / `filter_forward` | **iptables**, `mangle` chains `PORTMASTER-INGEST-OUTPUT` / `-INPUT`, `filter` chain `PORTMASTER-FILTER` |
| NFQUEUE | queue **0** (unit passes no flags; Go `int` default) | **17040** output, **17140** input |

The kill mechanism is that both hook OUTPUT/INPUT independently and both render
verdicts on the same packets, and Portmaster's chain ends in a default drop:

```
filter PORTMASTER-FILTER -m mark --mark 0 -j DROP
```

Anything Portmaster has not marked dies. With OpenSnitch also queueing and
answering, packets take verdicts from two engines that share no state. That is
what costs you the network in a TTY — not a queue collision.

Masking is therefore mandatory and must be unconditional, not
best-effort:

```bash
systemctl mask "${NON_SELECTED_UNIT}"
```

Masking (symlink to `/dev/null`) beats `disable` because it survives a
dependency pulling the unit in, and it is what the repo already does for
`libvirtd.service` (`build.sh:153`) for exactly this class of reason.

### Version pinning

**OpenSnitch (implemented).** Unchanged from today: `opensnitch_version` +
`opensnitch_sha256`, `curl`, `sha256sum -c`, `rpm2cpio | cpio`. Asserted at
runtime by `opensnitchd -version` in `tests/smoke.sh`.

**Portmaster (reserved).** The auto-updater is **config-disablable — no patch
required**, contrary to the brief's assumption:

- `core/automaticUpdates` — `service/core/update_config.go:24`, registered
  `:78–91`, `DefaultValue: true` at `:86`
- `core/automaticIntelUpdates` — `:25`, registered `:96–109`, default at `:104`
- Both flow to `Updater.Configure()` (`:131–134` → `service/updates/module.go:499`)

The `--disable-software-updates` flag referenced in the upstream unit's TODO
(`packaging/linux/portmaster.service:37`) **does not exist** — no match across
all `*.go`. It is also unnecessary: on Linux the binary updater already ships
`AutoDownload: false, AutoApply: false` (`service/config.go:158–171`), so it
checks and notifies but never replaces itself. Only the intel updater
auto-applies (`:187–201`), writing data — not executables — into `/var`.

The unresolved part is *delivery*, not capability: those keys live in
`/var/lib/portmaster/config.json`, so pinning requires first-boot seeding.
That is spike question 3.

## Rollback and variant switching

`rpm-ostree rollback` reverts `/usr` and the bootloader entry. It does **not**
revert:

| Path | Left behind | Consequence |
|---|---|---|
| `/etc/systemd/system/*.wants/*` | `systemctl enable` symlinks | A rolled-back image starts a daemon its build never enabled — the reason for the preset change |
| `/etc/opensnitchd/` | `default-config.json` (3-way merged), `rules/`, `system-fw.json` | Rules authored under one variant persist under another |
| `/var/lib/portmaster/` | config, intel data, databases, logs | Would survive a switch away from Portmaster entirely |
| `/var/log/opensnitchd.log` | log | Cosmetic |
| nftables/iptables rules | in-kernel state | Cleared on reboot, but *not* on a live switch |

A switch between variants therefore needs an explicit cleanup, because
neither daemon's packaging removes the other's state and `rpm-ostree` will not
either. Ship it as a `ujust` recipe alongside the existing ones in
`system_files/usr/share/ublue-os/just/60-custom.just`:

```just
# Remove state left by the *other* firewall variant. rpm-ostree rollback
# reverts /usr only — /etc and /var persist across a variant switch.
firewall-clean-state variant:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{variant}}" in
      opensnitch)  # we are now on opensnitch; purge portmaster leftovers
        sudo systemctl disable --now portmaster.service 2>/dev/null || true
        sudo rm -rf /var/lib/portmaster /etc/default/portmaster
        ;;
      portmaster)  # we are now on portmaster; purge opensnitch leftovers
        sudo systemctl disable --now opensnitch.service 2>/dev/null || true
        sudo rm -rf /etc/opensnitchd /var/log/opensnitchd.log
        ;;
      *) echo "usage: ujust firewall-clean-state [opensnitch|portmaster]" >&2; exit 1 ;;
    esac
    echo "Reboot to clear in-kernel netfilter state."
```

Deliberately **not** run automatically at boot: it deletes user-authored
firewall rules, and an image build has no business doing that silently. It is
an operator action, documented in `docs/RUNBOOK.md`.

## Event seam for an external consumer

Identified, not built — a later JSONL audit-log adapter attaches at exactly one
place per daemon:

| Daemon | Seam | Detail |
|---|---|---|
| OpenSnitch | **gRPC** | The daemon is the gRPC *client* and dials the UI. `Server.Address` in `default-config.json` — today `127.0.0.1:50051`, the Snitchwatch bridge. An adapter is a second gRPC server, or a shim in front of the bridge. |
| Portmaster | **local API + DB** | `DefaultAPIListenAddress = "127.0.0.1:817"` (`service/core/base/module.go:12`), plus the state DB under `/var/lib/portmaster/databases/`. |

The asymmetry matters for the eventual adapter: OpenSnitch *pushes* to a
listener it dials, Portmaster *exposes* an endpoint to be polled. A common
adapter interface has to accommodate both directions, so do not design the
JSONL writer around a push model alone.

## Systemd hardening

**Extend upstream, never replace.** Delivered as drop-ins under
`/usr/lib/systemd/system/<unit>.service.d/`, so an upstream unit change is not
silently overwritten.

OpenSnitch's shipped unit has **zero** hardening — 12 lines, `Type=simple`,
`ExecStart=/usr/bin/opensnitchd`, `Restart=always`. There is real headroom, but
every directive must be checked against what interception needs:
`CAP_NET_ADMIN`, `CAP_BPF` (if eBPF is ever re-enabled), host PID namespace
(process attribution reads `/proc`), and host mount namespace.

Portmaster's current packaging unit is already heavily hardened (`LockPersonality`,
`MemoryDenyWriteExecute`, `ProtectSystem=true`, `RestrictNamespaces`,
`ProtectKernelTunables`, explicit `CapabilityBoundingSet`) and is the better
starting template. Its writable-path directives are currently commented and
refer to `/var/lib/portmaster`; the image spike therefore uses an explicit
`StateDirectory=portmaster`. `Conflicts=firewalld.service` remains
load-bearing and must not be copied blindly.

Candidate drop-in for OpenSnitch, each line justified against `proc`-mode
process monitoring — **all of it unverified until tested**, since over-tight
sandboxing here fails as "no network," the exact outcome this PRP is trying to
avoid:

```ini
[Service]
NoNewPrivileges=yes
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectControlGroups=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_INET AF_INET6
# NOT set, and why:
#   ProtectSystem=strict  — daemon writes /etc/opensnitchd/rules/ at runtime
#   PrivateDevices=yes    — unverified against nfqueue; test before adding
#   ProtectProc=          — breaks proc-mode process attribution by design
```

## Test plan

Extending the existing gates rather than adding a new harness:

**`tests/smoke.sh`** — parameterize the OpenSnitch block on the selected
variant and add:

- selected daemon's unit is `enabled` *via preset*, not via an `/etc` symlink:
  `test ! -e /etc/systemd/system/multi-user.target.wants/opensnitch.service`
- non-selected daemon's unit is `masked`, reusing the existing `check_masked`
- preset fragment present and names both units
- exactly one firewall daemon binary present

**`tests/boot-check.sh`** — keep the hard exec assertion, and add that the
non-selected unit cannot be started.

**Build matrix** — `build.yml` already builds two variants off `BASE_IMAGE`.
Do **not** add a third axis for `FIREWALL_DAEMON` while only one value is
implemented; that doubles CI time to prove nothing. Add it with variant two.

## Open questions

1. **Preset scope** — option A or B above. Needs a decision before
   implementation.
2. **PRP location** — this file is at `docs/prp/` as specified in the brief,
   but the repo's existing planning artifacts live in `.claude/PRPs/`
   (`plans/`, `reports/`). Two locations is worse than either. Consolidate.
3. **Portmaster viability** — three questions, deferred to
   [the spike](portmaster-viability-spike.md). Not answerable from source.

## What this PRP does not claim

- That Portmaster works on this hardware. Nothing here has been booted.
- That the hardening drop-in is correct. Every directive is a hypothesis.
- That `FIREWALL_DAEMON=portmaster` will ever be implemented — that depends
  entirely on the spike's verdict, and "no" is a legitimate outcome.
