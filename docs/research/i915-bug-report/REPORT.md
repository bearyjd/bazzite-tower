# i915/MTL: C10 (cx0) PLL restored at parked idle clock on s2idle resume → eDP black-screen/flip_done storm (regression v6.19→v7.0, DPLL-framework conversion)

> **Status (2026-06-20):** the upstream issue is **confirmed** — [drm/i915/kernel #16042](https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/16042) ("Arrow Lake-P (P1 Gen 8) C10 PLL collapses to default state on s2idle resume — regression v6.19.13 → v7.0.3", Patrick Rodrigues, OPEN). It independently reaches this report's root cause. **Do NOT open a new issue** — post the additive comment in `COMMENT.md` there and attach `i915-warn-event-20260620.log`. This file is kept as the full standalone analysis. Still TODO before asserting hashes upstream: re-confirm `1a7fad2aea74` / `ac3423721117` against a local tree.
>
> **POSTED 2026-06-20** → [#16042 note_3529119](https://gitlab.freedesktop.org/drm/i915/kernel/-/work_items/16042#note_3529119) (with `i915-warn-event-20260620.log` attached; bisect offered).

## Summary

On Meteor Lake (MTL-P, eDP panel on pipe A, C10/cx0 PHY), resuming from s2idle leaves the C10 PLL **parked at its idle clock** (`port_clock` 61440 / multiplier 16) instead of being reprogrammed to the active HBR3 clock (810000 / multiplier 210). The atomic state check then fails (`mismatch in dpll_hw_state`, `mismatch in port_clock`), the eDP modeset times out (`flip_done timed out`, `PHY A failed to change powerdown state`), and the internal display is black/frozen for ~30s per wake until the driver eventually recovers. With `i915.enable_psr=0 i915.enable_dc=0` it degrades from a hard hang to the ~30s storm, but is never eliminated.

This is a **regression between v6.19 (good) and v7.0 (bad)**, reproduced here and by independent reporters on mainline/linux-zen 7.0.3–7.0.4 (see below).

## Hardware

| | |
|---|---|
| Machine | Lenovo ThinkPad P1 Gen 7 (21KV001CUS), BIOS N48ET33W 1.20 |
| CPU/SoC | Intel Core Ultra 9 185H (Meteor Lake-P) |
| iGPU | Intel Arc Graphics, `8086:7d55` (rev 08), subsystem `17aa:2234`, display version 14.00 stepping C0 |
| Panel | eDP-1 on pipe A, C10 PHY (PHY A), 4 lanes, HBR3 (810 MHz) |
| dGPU | NVIDIA (hybrid/Optimus) — **bug reproduces independently of NVIDIA** |

## Software

| | |
|---|---|
| Kernel | `7.0.9-ogc3.2.fc44.x86_64` (Open Gaming Collective build of 7.0.9; regression is in mainline 7.0.x display code — see external confirmations) |
| Driver | `i915` (modules present: `i915`, `xe`) |
| Distro | Fedora 44 atomic (Bazzite / Universal Blue) |
| Mesa | 26.1.0 |
| linux-firmware | 20260519 |
| DMC | `i915/mtl_dmc.bin` v2.23 |
| GuC / HuC | `mtl_guc_70.bin` 70.53.0 / `mtl_huc_gsc.bin` 8.5.4 |
| GSC | `mtl_gsc_1.bin` cv1.0 r102.1.15.1926 |

Suspend mode: **s2idle** (`/sys/power/mem_sleep` exposes only `s2idle`; MTL has no working S3 here).

Relevant kernel cmdline (UUIDs/ostree hash redacted):
```
... i915.enable_dc=0 i915.enable_psr=0 i915.enable_psr2_sel_fetch=0 mem_sleep_default=deep \
    intel_iommu=on iommu=pt ...
```
Notes: `mem_sleep_default=deep` is inert (only s2idle is available). `i915.enable_dc=0 / enable_psr=0 / enable_psr2_sel_fetch=0` are present and do **not** fix the bug — consistent with the fault being below the PSR/DC layer. `intel_iommu=on` is set here, but external reporters reproduce without it, so it is not the cause.

## Symptom — full dmesg of one resume cascade

```
i915 0000:00:02.0: [drm] *ERROR* Failed to bring PHY A to idle.
i915 0000:00:02.0: [drm] *ERROR* PHY A Read 0c70 failed after 3 retries.
i915 0000:00:02.0: [drm] *ERROR* PHY A Write 0c70 failed after 3 retries.
i915 0000:00:02.0: [drm] *ERROR* Timeout waiting for DDI BUF A to get active
i915 0000:00:02.0: [drm] *ERROR* Timed out waiting for DP idle patterns
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] flip_done timed out
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in pixel_rate (expected 777410, found 58968)
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in dpll_hw_state
i915 0000:00:02.0: [drm] *ERROR* expected:
i915 0000:00:02.0: [drm] *ERROR* cx0pll_hw_state: lane_count: 4, ssc_enabled: no, use_c10: yes, tbt_mode: no
i915 0000:00:02.0: [drm] *ERROR* c10pll_hw_state: clock: 810000, fracen: yes,
i915 0000:00:02.0: [drm] *ERROR* quot: 61440, rem: 0, den: 1,
i915 0000:00:02.0: [drm] *ERROR* multiplier: 210, tx_clk_div: 0.
i915 0000:00:02.0: [drm] *ERROR* c10pll_rawhw_state:
i915 0000:00:02.0: [drm] *ERROR* tx: 0x10, cmn: 0x21
i915 0000:00:02.0: [drm] *ERROR* found:
i915 0000:00:02.0: [drm] *ERROR* cx0pll_hw_state: lane_count: 4, ssc_enabled: no, use_c10: yes, tbt_mode: no
i915 0000:00:02.0: [drm] *ERROR* c10pll_hw_state: clock: 61440, fracen: no,
i915 0000:00:02.0: [drm] *ERROR* multiplier: 16, tx_clk_div: 0.
i915 0000:00:02.0: [drm] *ERROR* c10pll_rawhw_state:
i915 0000:00:02.0: [drm] *ERROR* tx: 0x0, cmn: 0x0
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in hw.pipe_mode.crtc_clock (expected 777410, found 58968)
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in hw.adjusted_mode.crtc_clock (expected 777410, found 58968)
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in port_clock (expected 810000, found 61440)
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] mismatch in min_voltage_level (expected 1, found 0)
i915 0000:00:02.0: [drm] *ERROR* flip_done timed out
i915 0000:00:02.0: [drm] *ERROR* [CRTC:149:pipe A] commit wait timed out
i915 0000:00:02.0: [drm] *ERROR* flip_done timed out
i915 0000:00:02.0: [drm] *ERROR* [CONNECTOR:506:eDP-1] commit wait timed out
i915 0000:00:02.0: [drm] *ERROR* flip_done timed out
i915 0000:00:02.0: [drm] *ERROR* [PLANE:33:plane 1A] commit wait timed out
i915 0000:00:02.0: [drm] PHY A failed to change powerdown state
```
The key line is the `found` PLL state: `c10pll clock 61440, multiplier 16`, `rawhw tx: 0x0, cmn: 0x0` — the PLL is parked/zeroed, i.e. **read back as-is and never reprogrammed to the expected 810000 / mult 210.** Reproduces on every s2idle resume (and on automatic display power-down/runtime-PM transitions).

## Enriched capture — verify-path stack traces + zeroed PLL (`drm.debug=0x100`, 2026-06-20)

Captured live on this host. Note the trigger here was **not** a full system suspend — it
was an **automatic deep-idle / runtime-PM display re-enable** (the panel powered down on
idle, then the warm restore on wake hit the same broken cx0 DPLL-restore path). So the bug
is the **DPLL state restore on display re-enable**, not strictly s2idle.

The atomic verify on resume (called from the compositor's `drm_mode_atomic_ioctl`) catches
the parked PLL but only **WARNs** — it never reprograms:

```
[drm] DPLL 0: pll hw state mismatch
WARNING: drivers/gpu/drm/i915/display/intel_dpll_mgr.c:4945 at verify_single_dpll_state+0x177/0x650 [i915], CPU#18: kwin_wayland/23415
RIP: 0010:verify_single_dpll_state+0x181/0x650 [i915]
Call Trace:
 intel_dpll_state_verify+0x6d/0x220 [i915]
 intel_modeset_verify_crtc+0x4f/0x80 [i915]
 intel_atomic_commit_tail+0x954/0xd10 [i915]
 intel_atomic_commit+0x23d/0x280 [i915]
 drm_atomic_commit+0xb1/0xe0
 drm_mode_atomic_ioctl+0x77f/0x8b0
 drm_ioctl+0x2d9/0x560
 __x64_sys_ioctl+0xb9/0x100
 do_syscall_64 ...
---[ end trace ]---
```
(A preceding `verify_crtc_state+0x2b3` WARNING at `intel_modeset_verify.c:225` —
"pipe state doesn't match!" — fires on the same commit. Taint flags `P U W O` are from
out-of-tree NVIDIA/system76 modules, unrelated to this fault.)

**The decisive evidence** — the `found` PLL registers are entirely zero (PHY powered down),
i.e. the resume readout adopts the parked state verbatim and the reprogram never runs:

```
expected:  c10pll clock 810000, multiplier 210, tx:0x10 cmn:0x21
           pll[0]=0x34 pll[2]=0x84 pll[9]=0x1 pll[12]=0xf0 pll[16]=0x84 pll[17]=0xf pll[18]=0xe5 pll[19]=0x23 ...
found:     c10pll clock  61440, multiplier  16, tx:0x0  cmn:0x0
           pll[0..19] = 0x0   <-- entirely zero / parked
-> mismatch in port_clock (expected 810000, found 61440); flip_done timed out; PHY A failed to change powerdown state
```

This confirms the code-reading hypothesis at runtime: the v7.0 DPLL framework reads the
powered-down PLL back as "current", the warn-only `verify_single_dpll_state` `memcmp` does
not force a reprogram, and the cx0-specific verify that v6.19 used to catch this was removed
by `ac3423721117`. Full untrimmed log: `i915-warn-event-20260620.log`.

## Regression range

| Kernel | Result |
|---|---|
| v6.19.x (6.19.13 / 6.19.14) | **GOOD** — clean resume |
| v7.0.x (7.0.3, 7.0.4, this 7.0.9-ogc, Ubuntu 7.0.0-14) | **BAD** — the cascade above |

Independent confirmations on plain mainline/linux-zen (not just this distro build):
- Arch BBS — P1 Gen 7 (MTL) + P1 Gen 8 (ARL): broken 7.0.3/7.0.4, good 6.19.14 — https://bbs.archlinux.org/viewtopic.php?pid=2297604
- omarchy #5695 — P1 Gen 7 (155H): broken 7.0.3, good 6.19.13 — https://github.com/basecamp/omarchy/issues/5695
- Ubuntu bug 2150605 — Arrow Lake-S (`8086:7d67`), identical `clock 810000 expected / 61440 found` resume mismatch — https://www.mail-archive.com/ubuntu-bugs@lists.ubuntu.com/msg6272941.html

## Suspected cause (NOT bisect-proven — offered as a lead)

The cx0→DPLL-framework conversion series in v7.0 is the **only cx0 change in the v6.19→v7.0 window** and matches the symptom. Lead commit **`1a7fad2aea74`** ("drm/i915/cx0: Enable dpll framework for MTL+"), with **`ac3423721117`** ("drm/i915/cx0: Remove state verification", −114 lines) deleting the cx0-specific `intel_c10pll_state_verify`/`intel_cx0pll_state_verify` per-register readback. All are present in v7.0, absent in v6.19, and **unreverted in master**. (The earlier community guess that the powerdown-timeout commit `fc9be0a10ca4` is at fault is incorrect — its fix `50101556` `_US`→`_MS` is already in both 6.19 and 7.0; and the guessed fix `afe3f7471623` is an unrelated DP-tunnel resume change.)

Code-reading hypothesis (pending runtime confirmation): on resume `readout_dpll_hw_state()` adopts the parked PLL registers as the SW "current" state; `sanitize_dpll_state()` only force-disables when `pll->on && !pll->active_mask`, so a parked-but-claimed-active eDP PLL is kept; and `verify_single_dpll_state()` is a warn-only `memcmp` of HW-against-itself-after-readout, so the parked clock is never corrected — whereas the deleted cx0-specific verify in v6.19 would have caught/forced it. This is a framework readout/reconcile gap, not the powerdown timeout.

## What I can provide

- **Exact repro hardware** (P1 Gen 7 MTL-P) and fast turnaround to **test candidate patches**.
- A **`drm.debug=0x100` (DRM_UT_KMS)** capture across one suspend/resume on request — specifically to confirm whether `readout_dpll_hw_state` logs the eDP PLL as `on` with the pipe in `active_mask`, and whether the divider-verify warn (`58213c1d781c`) fires `810000` vs `61440` while the framework `memcmp` warn does not.
- A **git bisect v6.19→v7.0** if wanted (must be on this hardware — the bug needs the real MTL display PHY; it does not reproduce in a VM).

---
*Generated from a real failing host on 2026-06-20. Raw capture: `raw-capture.txt`.*
