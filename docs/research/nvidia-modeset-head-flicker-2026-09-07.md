# Occasional display flicker ↔ `cardwired` dGPU power-cycling — Research Report

**Date:** 2026-09-07
**Upstream:** nothing filed. `cardwire` is [Open Gaming Collective's](https://github.com/ublue-os)
eBPF-based GPU-switching daemon (D-Bus name `org.opengamingcollective.cardwire`), described by its
own README as "early development."
**Status:** **The `nvidia-modeset` warnings are RULED OUT as the cause of the flicker.** Two
successive theories were tested and both failed (Experiments 1 and 2 below). The flicker occurs
with zero warnings in the preceding five minutes and produces **no log entry in either GPU driver**
at default verbosity. Open, cause unknown. No durable config change made.

> **Read this first if you are picking the investigation up cold.** This document records three
> theories, two of which were disproven by experiment and one of which was disproven by a single
> user observation. The disproofs are kept in full, because each looked convincing beforehand. The
> `nvidia-modeset: Correcting number of heads` warnings are real, frequent, and **not the flicker**
> — do not restart the investigation from them.

## Executive Summary

User-reported symptom (2026-09-06, confirmed NEW): an occasional horizontal line that sweeps
top-to-bottom across the screen. Brief, cosmetic, nothing breaks or loses state.

The recurring log signature is `nvidia-modeset: WARNING: GPU:0: Correcting number of heads for
current head configuration (0x00)` — emitted for a discrete GPU that **drives no physical display
on this machine**.

**What is established:**

1. The warnings correlate with **active desktop interaction**, not with time, uptime, or power
   state. A 13-minute controlled test (below) produced 95 warnings in a continuous 5-minute storm
   at 1–3 second intervals while the user actively switched windows, then **complete silence** for
   the following 7 idle minutes.
2. **`kwin_wayland` is the only process holding `/dev/nvidia-modeset` open** (`fuser -v`), so the
   compositor is what pokes the NVIDIA modeset device.
3. The user's own sighting reports place flickers during window switching — "it was in switching
   windows from terminal back to another app."

**What was DISPROVEN:** an earlier revision of this doc concluded the cause was `cardwired`
power-cycling the dGPU (2,367 D3Cold↔D0 transitions/day, with warnings trailing wakes by 1–15s).
That correlation was real but **not causal** — see "Experiment 1" below. Pinning the GPU awake
eliminated 100% of power transitions and the warnings continued anyway, at a *higher* rate.

**Still unproven:** that the warnings and the visible flicker share a cause. They co-occur during
active use, but no single flicker has been timestamped to a single log line.

Note `cardwired` **is** the component that changed in the 2026-08-28 base-image switch to
`bazzite-nvidia-open` (it replaced `supergfxctl`), which matches "this is new" — but per
Experiment 1 it is not the mechanism.

## Experiment 1 — pin the dGPU awake (DISPROVED the power theory)

**Hypothesis:** warnings are caused by `D3Cold → D0` wake transitions, so preventing sleep will
stop them.

**Method:** `echo on > /sys/bus/pci/devices/0000:01:00.0/power/control` at 23:08:45, reverted to
`auto` at 23:12. Runtime-only, no reboot.

**Result — hypothesis refuted:**

| Metric | Value |
|---|---|
| Power transitions during test | **0** (pin held; `runtime_status=active` throughout) |
| Warnings during test | **9** in ~3 minutes (~180/hr) |
| Warning rate before test | ~22/hr |

Applying the pin did produce exactly one final wake→warning pair (23:08:46 `D0` → 23:08:48
warning), which is consistent with wakes being *one* trigger. But with wakes then eliminated
entirely, warnings continued **and accelerated**. A wake is therefore neither necessary nor
sufficient. The user also observed a flicker during this window, with the counter provably
unaffected by power state.

**Conclusion:** dGPU runtime power management is ruled out as the cause of both the warnings and
the flicker. `cardwired`'s transition volume remains anomalous and worth attention on its own
merits, but it is a separate issue.

## Experiment 2 — deliberate window switching (activity correlation)

**Method:** baseline recorded at 23:12:53 (336 warnings), user deliberately switched windows
(terminal ↔ Claude Cowork ↔ browser) for several minutes, reported "a few flickers."

**Result:** 336 → 431 = **95 warnings in 13 minutes.**

```
23:13:52 → 23:18:53   near-continuous, one warning every 1–3 seconds
23:18:53 → 23:25:38   zero — completely silent once interaction stopped
```

**Interpretation:** the trigger is *active desktop interaction*, not discrete window-switch events
(95 warnings is far more than the ~10 switches performed, and they are evenly spaced rather than
clustered per switch). The silence during the following 7 idle minutes is as informative as the
storm. This also retroactively explains the "burst / quiet / burst" shape across both boots: the
bursts are simply when the machine was being used.

**Not established:** whether each warning corresponds to a visible flicker. The user saw "a few"
flickers against 95 warnings, so at most a small fraction are visible events — or the two are
independent symptoms of one underlying compositor/driver interaction.

## Experiment 3 — the idle flicker (DISPROVED the activity correlation, and rules out the warnings)

**Observation, 2026-09-07 ~23:26:** the user reported a flicker while **idle** — "it just happened
and I was doing nothing... just watching this terminal window."

**Check at 23:26:54:**

| Metric | Value |
|---|---|
| `Correcting number of heads` warnings in preceding 5 min | **0** |
| Any i915 display error in that window | **none** |
| Total kernel activity in that window | 3 lines, all a Bluetooth mouse reconnecting |

**Conclusion — this is the decisive result of the whole investigation:** a flicker occurred with
**zero** modeset warnings in the preceding five minutes. The warnings and the flicker are
therefore **independent phenomena**. Experiment 2's activity correlation described the *warnings*
accurately but says nothing about the flicker.

**The NVIDIA line of investigation is closed for this symptom.** The warnings remain a real,
high-volume, unexplained log annoyance (see "Actionable paths"), but they are not what the user
sees.

## What the flicker actually looks like, evidentially

After three experiments, the symptom's distinguishing property is **its complete absence from the
logs**:

- Horizontal line sweeping top→bottom, brief, cosmetic, no state lost.
- Occurs during **both** active use and complete idle.
- **No `nvidia-modeset` warning** at the time (Experiment 3).
- **No i915 error** — this entire boot produced exactly *one* i915 display error
  (`[CRTC:151:pipe A] DSB 0 poll error`, at boot) out of 31 i915 lines total.
- `drm.debug` is `0`, so the DRM/KMS layer is logging nothing about normal atomic commits.

A rolling horizontal line with no driver error is characteristic of a **frame presented
mid-scanout** (a tear or frame-pacing artifact) rather than a driver fault. Consistent with, but
not proven by, Chrome's `Frame latency is negative` compositor errors.

Ruled out on the compositor side: `AllowTearing` is unset (KWin default), `LatencyPolicy=Medium`,
and `vrrPolicy: "Never"` on every output in `~/.config/kwinoutputconfig.json`.

## ACTIVE CAPTURE — drm.debug enabled 2026-09-07 23:42

Since the symptom is invisible at default verbosity, `drm.debug` is now raised at runtime and
left on to catch the next sighting.

**Tool:** [`scripts/i915-drm-debug-capture.sh`](../../scripts/i915-drm-debug-capture.sh) — now
**tracked in the repo**, not a loose host script. (The previous host-side helper,
`~/scripts/i915-drm-debug-capture.sh`, was deleted during the 2026-09-07 cleanup as "closed
investigation" scaffolding and was needed again hours later. Keeping it in-repo is the fix.)

**State as of 2026-09-07 23:42:** `drm.debug = 0x104` (KMS + DP), verified emitting
(`[drm:i915_dpt_create]`, `[drm:drm_mode_addfb2]`).

**Procedure when a flicker is seen — note the wall-clock time, then:**

```bash
sudo ./scripts/i915-drm-debug-capture.sh grab 23:26      # +/-60s window
sudo ./scripts/i915-drm-debug-capture.sh grab 23:26 120  # wider window
sudo ./scripts/i915-drm-debug-capture.sh off             # when done
```

**Measured cost:** 508 kernel lines / 5 min ≈ 17 MB of journal per day, against the 4G cap in
`90-tower-journal-cap.conf` (currently 1G used). Safe to leave running for days.

**Mask notes:** `0x104` = KMS + DP — modeset/pipe activity plus eDP link training and AUX, where a
panel-side glitch would surface. If a capture shows nothing, try `0x114` to add ATOMIC (per-commit
detail). **Never add VBL (`0x20`)** — at 165 Hz that is ~165 lines/second and will flood the
journal and evict other evidence.

**What to look for in a capture:** anything that is *not* the steady-state
`i915_dpt_create`/`drm_mode_addfb2` framebuffer churn — specifically link training, AUX errors,
pipe/plane underruns, DSB errors, or an unexpected modeset — within a second or two of the
reported time. The baseline in-window content is already known to be that dpt/addfb2 pair repeating,
so anything else is signal.

Method background: `docs/research/i915-bug-report/BISECT-RUNBOOK.md`.

## The wake correlation (SUPERSEDED — kept because it is instructive)

**This section documents the reasoning that Experiment 1 refuted.** It is retained deliberately:
the correlation below is genuine and reproducible, and it still misled. Treat it as a caution
about accepting temporal correlation as mechanism.

`journalctl -b 0` on 2026-09-07, dGPU wake events vs. modeset warnings:

```
16:17:47  cardwired: NVIDIA GeForce RTX 4070 Laptop GPU: Power state changed: D0
16:17:48  kernel: nvidia-modeset: WARNING: GPU:0: Correcting number of heads ...   (+1s)
16:18:09  cardwired: ... D3Hot  →  D3Cold
16:19:06  cardwired: ... D0
16:19:21  kernel: nvidia-modeset: WARNING: ... Correcting number of heads ...      (+15s)
16:19:28  cardwired: ... D3Hot  →  D3Cold
16:27:26  cardwired: ... D0
16:27:27  kernel: nvidia-modeset: WARNING: ... Correcting number of heads ...      (+1s)
```

**Every** warning is preceded by a wake. The converse is not true — 2,367 transitions produced only
31 warnings, so a wake is **necessary but not sufficient**. Whatever additional condition turns a
wake into a warning (and possibly into a visible glitch) is not yet identified.

Transition volume, `cardwired` "Power state changed" lines per hour:

| Hour | Count | | Hour | Count |
|---|---|---|---|---|
| 08 (boot) | 5 | | 14 | 225 |
| 09 | 116 | | 15 | 236 |
| 10 | 219 | | 16 | 188 |
| 11 | 321 | | 17 | 343 |
| 12 | 319 | | 18 | 44 |
| 13 | 320 | | 19 | 31 |

**Total 2,367.** For a GPU that drives no physical display on this machine, this is an extraordinary
amount of power-state churn.

### Likely aggravating factor: two daemons with opposing jobs

`fuser -v /dev/nvidia*` shows seven processes holding the device open:

```
/dev/nvidia0:  cardwired(1693)  nvidia-powerd(2719)  nvidia-persistenced(2752)
               kwin_wayland(5707)  plasmashell(6078)  chrome(7882)  Xwayland(1396837)
```

**`nvidia-persistenced` exists to keep the GPU initialized; `cardwired` exists to power it down.**
Both are running. With five additional clients (compositor, shell, browser, Xwayland) each able to
touch the device and trigger a wake, 2,367 transitions/day is consistent with these two fighting.
UNVERIFIED as an actual conflict — stated as the most plausible reading of the numbers, not a
measured finding.

## Secondary signature: Chrome compositor frame-timing errors

`chrome://gpu` (Chrome 152.0.7977.82, Flatpak) "Log Messages", same day:

| Time | Value |
|---|---|
| 08:47:50 | -0.159 ms |
| 14:43:10 | **-8.524 ms** |
| 14:46:05 | -0.026 ms |
| 14:46:07 | -0.107 ms |
| 14:56:17 | -0.021 ms |

`Frame latency is negative` (`components/viz/service/display/display.cc:271`) means Chrome's viz
compositor computed a presentation timestamp *earlier* than the frame was scheduled — a real
timing anomaly, and a more direct candidate for a visible artifact than a driver-internal warning.
These do **not** align 1:1 with the modeset warnings (the 14:43–14:56 cluster has no matching
kernel line), which is consistent with both being downstream symptoms of the same GPU power churn
rather than one causing the other.

Note `chrome://gpu` also confirms **GPU1 (Intel MTL `8086:7d55`) is Chrome's `*ACTIVE*` renderer**,
not the NVIDIA dGPU. So this is not "Chrome offloading to the dGPU" — Chrome renders on the iGPU
and is merely one of several processes holding the NVIDIA device open.

## Ruled out

- **VRR** — `kscreen-doctor -o` reports `Vrr: Never` on eDP-1. Not involved.
- **HDR / wide color gamut / ICC** — all `disabled`, color profile source `sRGB`. Not involved.
- **`evdi` virtual displays** — the module is loaded but has **use count 0** and instantiates no DRM
  device (`/sys/class/drm/` holds only `card0`=nvidia, `card1`=i915). Investigated because `evdi`
  initialized at 08:47:24, one second before a warning burst; that proved to be coincidence — 08:47
  is simply when boot resumed after the LUKS passphrase was entered (kernel started 08:30:41, sat
  at the prompt ~17 min), so that burst is ordinary boot-time display init.
- **PSR/DC kargs** — `i915.enable_dc=0`, `i915.enable_psr=0`, `i915.enable_psr2_sel_fetch=0` were
  active on every boot in this report. The symptom occurs *with* that mitigation in place, so it is
  neither caused nor prevented by those flags.

## Environment

- ThinkPad P1 Gen 7, Meteor Lake iGPU (`8086:7d55`, i915, `card1`, drives the eDP-1 panel at
  2560x1600@165.02, scale 1.25) + NVIDIA RTX 4070 Laptop (`10de:2820`, `card0`, drives **no**
  physical output).
- Base image `bazzite-nvidia-open`, switched 2026-08-28 (see `Containerfile` header and
  `docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md`). Kernel `7.2.0-ogc6.1.fc44`,
  NVIDIA driver `610.57.04` (NFB).
- `cardwired.service` — **enabled and running**, `/usr/lib/systemd/system/cardwired.service`,
  `Type=dbus`, `BusName=org.opengamingcollective.cardwire`, active since boot. Ships in the base
  image; this repo does not install, configure, or disable it.
  *(An earlier revision of this doc wrongly recorded cardwire as "not running as a persistent
  service" — that check used the wrong unit name (`cardwire` vs `cardwired`) and its conclusion was
  false. Corrected here.)*
- i915 runtime PM: `power/control=auto`, `autosuspend_delay_ms=10000`, `runtime_status=active`.
  The iGPU also runtime-suspends (`cardwired` logs `Intel: Power state changed: D3Hot/D0`), which
  is normal, but is a second display-adjacent power path worth keeping in mind.

## The PSR/DC karg test never ran — do not confuse it with this

A single-variable test was staged 2026-08-31 (`rpm-ostree kargs --delete=i915.enable_dc=0 ...`) to
check whether `10-i915-display.toml` is still needed post the cx0/DPLL fix. **It never booted.** The
machine stayed up 7 more days, during which the weekly `:latest` rebuild (`latest.20260906`,
package-only bump) auto-staged and silently superseded the karg-only deployment. The 09-07 boot is
that package build, with all three kargs still applied.

**Lesson:** a `rpm-ostree kargs`-only staged deployment does not survive an intervening
`bootc upgrade`. Re-stage a karg test immediately before the intended reboot, not in advance.

## Monitoring

```bash
# dGPU power-cycle volume (the headline number)
journalctl -b 0 -t cardwired --no-pager | grep -c "Power state changed"

# Per-hour breakdown
journalctl -b 0 -t cardwired --no-pager -o short-iso | grep "Power state changed" \
  | awk '{print substr($1,1,13)}' | sort | uniq -c

# Modeset warnings, and whether each still trails a D0 wake
journalctl -k -b 0 --no-pager | grep -c "Correcting number of heads"
journalctl -b 0 --no-pager -o short-iso | grep -E "cardwired.*Power state|Correcting number of heads"
```

Also check `chrome://gpu` → "Log Messages" for new `Frame latency is negative` entries.

**The one measurement still missing:** a user-observed flicker with a wall-clock time attached. With
that, the above query answers definitively whether the sighting sits on a `D0` wake. Everything else
here is already established.

## ACTIVE EXPERIMENT — dGPU pinned awake (started 2026-09-07 23:08:45)

**Change applied (runtime only, does NOT survive reboot):**

```bash
echo on | sudo tee /sys/bus/pci/devices/0000:01:00.0/power/control
```

This pins the discrete GPU in D0 so no `D3Cold → D0` wake transitions can occur. If wakes are what
produce the warnings (and the flicker), both should stop dead.

**Baseline at test start, same boot (booted 2026-09-07 08:30:41):**

| Metric | ~18:03 | 23:08:45 (test start) |
|---|---|---|
| `cardwired` power-state transitions | 2,367 | **3,099** |
| `Correcting number of heads` warnings | 31 | **325** |

Note the warning count escalated 31 → 325 in ~5 hours, mirroring Boot A's climb into the hundreds.
The rate is not stable over a boot; **compare rates over comparable windows, not raw totals.**

**Applying the pin produced exactly one final wake→warning pair, as predicted:**

```
23:08:32  cardwired: NVIDIA ...: Power state changed: D3Cold   (asleep)
23:08:46  cardwired: NVIDIA ...: Power state changed: D0       (the pin forcing a resume)
23:08:48  kernel: nvidia-modeset: WARNING: ... Correcting number of heads ...   (+2s)
```

**Reading the result** — counts should now be frozen at 3,100 / 326:

```bash
journalctl -b 0 -t cardwired --no-pager --since '2026-09-07 23:08:50' | grep -c "Power state changed"
journalctl -k -b 0 --no-pager --since '2026-09-07 23:08:50' | grep -c "Correcting number of heads"
```

- **Both stay 0 and the flicker stops** → causation established; move to a durable fix (see paths below).
- **Both stay 0 but the flicker continues** → the wake/warning chain is a red herring; the flicker
  has another cause and this whole line of investigation is ruled out. Equally valuable.
- **Counts climb anyway** → something is overriding the pin; re-check `power/control`.

**Revert (immediate, no reboot):**

```bash
echo auto | sudo tee /sys/bus/pci/devices/0000:01:00.0/power/control
```

Cost while pinned: the dGPU idles in D0 instead of D3Cold — a few watts, and it forfeits the ~92%
suspended residency measured earlier. Fine on AC, worth reverting before running on battery.

## Actionable paths (none taken — observing)

1. **Reduce the churn.** The 2,367/day transition rate is the anomaly worth attacking regardless of
   whether it is the flicker's proximate cause. Options: stop/mask `cardwired.service` (it ships in
   the base image, so this is a local `/etc` change or a new `build.d` step, and it would forfeit
   whatever GPU-switching behavior it provides), or find and stop whatever repeatedly wakes the
   dGPU. **Masking `cardwired` is the single cheapest A/B test available** — if the warnings and the
   flicker both stop, causation is settled.
2. **Investigate the `nvidia-persistenced` vs `cardwired` opposition.** If confirmed, disabling
   persistence mode may cut the churn without giving up GPU switching.
3. **Revert the base-image flavor** to proprietary `bazzite-nvidia` (which uses `supergfxctl`
   instead). Heaviest option; the `Containerfile` already documents `bootc rollback` / re-pinning as
   the intended mitigation if the 08-28 switch regressed in practice.
4. **File upstream against `cardwire`** once there is a clean repro — the transition volume alone
   is arguably a reportable bug even without the flicker.

## What NOT to do

- Do not change `10-i915-display.toml` on the strength of this report — those kargs are demonstrably
  orthogonal to this symptom (see "Ruled out").
- Do not treat the mechanism as proof of the visible symptom. A wake is necessary but not sufficient
  even for the *log warning* (2,367 wakes → 31 warnings); the link from warning to visible flicker
  is still inference.
