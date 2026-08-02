# Spike — is Portmaster viable on this image at all?

> **This is a spike, not a plan.** It exists to produce a yes/no verdict on
> whether `FIREWALL_DAEMON=portmaster` is worth designing around. "No" is a
> legitimate and expected outcome; the point is to stop guessing.
>
> Prerequisite reading: [parameterized-firewall-module.md](parameterized-firewall-module.md).

## Why a spike instead of more design

Phase 1 read `safing/portmaster` at `v2.2.1` end to end and answered every
question that source can answer. What remains are three runtime questions that
source cannot settle, and all three are load-bearing: if any answer is "no,"
the variant is not worth building, and no amount of further design changes
that.

Designing around unknowns here is expensive because the failure mode is
"machine has no network," and the machine in question is a daily driver.

## Hard rule

**Never boot this on the ThinkPad first.** Portmaster's filter chain ends in
`filter PORTMASTER-FILTER -m mark --mark 0 -j DROP` — anything it has not
marked is dropped. A misconfigured daemon that starts at boot and drops
traffic, on a laptop whose recovery path is a TTY, is the exact scenario the
parent PRP's masking requirement exists to prevent.

Every step below runs in a VM via `just build-qcow2` + `just run-vm-qcow2`.
Both need sudo + KVM and are slow; that cost is the point.

## Step 0 — build the daemon (no Earthly, no RPM)

Confirms the sourcing conclusion from Phase 1 in practice.

```bash
git clone --depth 1 --branch v2.2.1 https://github.com/safing/portmaster.git
cd portmaster
CGO_ENABLED=0 go build \
  -ldflags="-X github.com/safing/portmaster/base/info.version=2.2.1 \
            -X github.com/safing/portmaster/base/info.buildSource=bazzite-tower-spike \
            -X github.com/safing/portmaster/base/info.buildTime=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -o ./portmaster-core ./cmds/portmaster-core
sha256sum ./portmaster-core
```

**Pass:** a binary is produced without Earthly, Rust, or Node.
**Fail:** the daemon pulls in GUI build machinery → sourcing is harder than
Phase 1 concluded; reassess before continuing.

Note the `buildTime` makes this non-reproducible byte-for-byte. If the variant
proceeds, pin `buildTime` to a fixed value so the image is reproducible — the
version string is what matters, not the timestamp.

### RESULT (2026-08-01): PASS

Built in a `docker.io/library/golang:1.24` container against the clone at tag
`v2.2.1`, with `buildTime` pinned to `2026-08-01T00:00:00Z`:

```
Portmaster 2.2.1
built with go1.24.13 (gc -cgo) for linux/amd64
  at 2026-08-01T00:00:00Z
commit af0c60140ec4a5d7239aaf61bb3d81ac3c56e51b (clean)
  from bazzite-tower-spike
```

- 45,604,649 bytes, `sha256:5f93e309cfbb56a05dcdc8c1bc7825de15cded8abcc3127233936bff3778d0be`
- **Statically linked** (`CGO_ENABLED=0` → `not a dynamic executable`). This
  matters: the failure that broke OpenSnitch — an unresolved `DT_NEEDED`
  library the extraction never installed — is structurally impossible here.
- The binary self-reports its source commit and clean-tree status, which is
  stronger provenance than the OpenSnitch RPM offers.

Two static findings that de-risked this before the build ran:

- The embedded eBPF objects (`bpf_bpfel.o`, `bpf_bpfeb.o`) are **committed to
  git**, not generated — so no clang or bpf2go is needed. This was the most
  plausible way the "plain `go build`" conclusion could have been wrong.
- No Angular/Tauri assets are embedded anywhere in the daemon tree; the only
  other `go:embed` is `base/database/storage/sqlite/migrations/*`.

**Conclusion: sourcing is fully solved and is not a reason to defer the
variant.** The deferral rests entirely on Q1–Q3 below.

The built artifact is not committed — it lives outside the repo by design.
Reproduce it with the command above; the hash should match given the same tag
and pinned `buildTime`.

## Question 1 — DNS coexistence (highest risk, run first)

**Why it decides everything:** Portmaster binds `localhost:53`
(`service/nameserver/config.go:15`) and hunts for competitors on `0.0.0.0`,
`127.0.0.1`, `127.0.0.53`, `::`, `::1` (`service/nameserver/conflict.go`).
This host runs Tailscale MagicDNS, NextDNS, and TorGuard.

**Setup:** VM with the real stack — Tailscale up with MagicDNS enabled,
NextDNS configured as on the host, TorGuard installed.

**Procedure:**

```bash
# before starting portmaster
ss -lunp 'sport = :53'          # who owns 53 already
resolvectl status                # per-link resolvers, MagicDNS entry
tailscale status --json | jq .CurrentTailnet.MagicDNSSuffix

sudo systemctl start portmaster.service

# after
ss -lunp 'sport = :53'
resolvectl status
journalctl -u portmaster -b | grep -i "conflict\|nameserver\|resolver"
resolve() { getent hosts "$1" >/dev/null && echo "OK $1" || echo "FAIL $1"; }
resolve <a-magicdns-host>.<tailnet>.ts.net
resolve example.com
resolve <an-internal-nextdns-blocked-domain>
```

**Pass:** MagicDNS names still resolve, public DNS still resolves, NextDNS
filtering still applies, and Portmaster logs no unresolved conflict.
**Fail:** any of the four breaks, *or* Portmaster logs a conflicting process
and declines to bind. **Fail here ends the spike** — record the verdict and
stop. Do not attempt workarounds; a firewall that has to be fought into
coexisting with the DNS stack is not a maintainable default on this box.

## Question 2 — does it start with `/usr` read-only?

**Why:** the upstream unit declares `ReadWritePaths=/usr/lib/portmaster`
(`packaging/linux/portmaster.service:26`). On bootc, `/usr` is read-only.
Unclear whether systemd refuses to start the unit, or whether it starts and
only fails if something actually writes.

**Procedure:**

```bash
sudo systemctl start portmaster.service
systemctl is-active portmaster.service
journalctl -u portmaster -b | grep -iE "read-only|ReadWritePaths|namespace|failed"
sudo touch /usr/lib/portmaster/.write-probe   # expected to fail
```

**Pass:** unit reaches `active` and logs no namespace-setup failure.
**Partial:** starts only after dropping `ReadWritePaths` in a drop-in — record
the exact override needed; this becomes a required part of the design.
**Fail:** cannot start without a writable `/usr`. That is disqualifying for a
bootc image.

## Question 3 — does the update pin actually stick?

**Why:** `core/automaticUpdates` and `core/automaticIntelUpdates`
(`service/core/update_config.go:24–25`) are the pin, but they persist to
`/var/lib/portmaster/config.json` (`base/config/main.go:40`) — outside the
image. The design depends on seeding them at first boot and having them hold.

**Procedure:**

```bash
sudo systemctl stop portmaster.service
sudo install -Dm600 /dev/stdin /var/lib/portmaster/config.json <<'EOF'
{ "core": { "automaticUpdates": false, "automaticIntelUpdates": false } }
EOF
sudo systemctl start portmaster.service
sleep 120

# did the daemon keep the values, or rewrite them?
sudo jq '.core' /var/lib/portmaster/config.json
# did it reach out anyway?
journalctl -u portmaster -b | grep -iE "update|updates.safing.io|index"
sudo ls -la /var/lib/portmaster/download_binaries /var/lib/portmaster/download_intel 2>/dev/null
```

**Pass:** both values survive a restart and no download directories appear.
**Fail:** the daemon rewrites the file, ignores the keys, or downloads anyway
→ the version pin cannot be enforced, which the brief rules out as
unacceptable for an image-based system.

Note the nested-vs-slashed key form is worth confirming: keys register as
`core/automaticUpdates` but `ReleaseChannelJSONKey` (`:12`) shows the JSON form
is dotted/nested (`core.releaseChannel`). If the seed above has no effect, try
the flat `"core/automaticUpdates": false` form before calling it a fail.

## Verdict

**Progress: Step 0 PASS (2026-08-01). Q1–Q3 not run.** Q1 requires a VM
carrying this operator's real Tailscale/NextDNS/TorGuard identity and cannot
be delegated; Q2 and Q3 are gated behind it by design, since a Q1 failure ends
the spike outright.

| Q1 DNS | Q2 `/usr` | Q3 pin | Verdict |
|---|---|---|---|
| Pass | Pass | Pass | **Proceed** — extend the parent PRP to implement `FIREWALL_DAEMON=portmaster` |
| Pass | Partial | Pass | **Proceed with conditions** — record the required drop-in override as a design constraint |
| Fail | — | — | **Stop.** Record and close; do not attempt DNS workarounds |
| — | Fail | — | **Stop.** Disqualifying for bootc |
| — | — | Fail | **Stop.** Violates the version-pinning requirement |

Q1 runs first precisely because it is both the likeliest failure and the one
that makes the other two moot.

## Deliverable

A dated findings note under `docs/research/` following the shape of
`docs/research/i915-bug-report/` — raw captures (`ss`, `resolvectl`,
`journalctl` excerpts) alongside the writeup, with claims labeled VERIFIED vs
INFERRED. That directory is this repo's worked example of a hardware/runtime
investigation done properly; match it.

If the verdict is "stop," the note is still the deliverable — a recorded
negative result is what stops this question being re-litigated in six months.
