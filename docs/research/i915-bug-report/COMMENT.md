# Comment for drm/i915/kernel #16042 (Patrick Rodrigues — ARL-P P1 Gen 8 C10 PLL resume collapse)

> https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/16042
> Paste as a COMMENT on #16042 (additive confirmation + bisect offer — NOT a new issue). Attach `i915-warn-event-20260620.log`.

---

**Second confirmation — Meteor Lake-P (ThinkPad P1 Gen 7, `8086:7d55`), different distro + later kernel, with a native stack trace and an offer to bisect.**

Same regression, independent machine. Adds a direct GitLab data point for **MTL-P** (currently referenced here only via Fedora #2463170), on a non-Arch kernel build, and confirms it's **still present in 7.0.9** (later than the 7.0.3 in the report).

**System**
- Lenovo ThinkPad P1 Gen 7 (21KV001CUS), Core Ultra 9 185H, **Meteor Lake-P**, iGPU **`8086:7d55`** (display v14.00 stepping C0), internal **eDP-1 on pipe A, C10 PHY**. NVIDIA dGPU present but not on the failing path.
- Kernel **7.0.9** (Open Gaming Collective build; Fedora 44 / Bazzite atomic), driver `i915`. DMC `mtl_dmc.bin` v2.23, GuC `mtl_guc_70.bin` 70.53.0, HuC `mtl_huc_gsc.bin` 8.5.4. Last-good line: **6.19.x** — matches the v6.19.13 → v7.0.3 boundary exactly.
- Same "what does NOT help": cmdline already carries `i915.enable_dc=0 enable_psr=0 enable_psr2_sel_fetch=0`; no effect, identical to your finding.

**Matching fingerprint.** The verifier's `found` C10 state is the post-reset default precisely as you describe — `c10pll clock 61440, multiplier 16, fracen no, tx:0x0, cmn:0x0, pll[0..19]=0x0` — versus a fully-populated `expected`. One useful difference: our panel runs **port_clock 810000 (HBR3, mult 210)** vs your 540000/140, and it collapses to the same 61440 — so the failure is **not link-rate-specific**.

**Native stack trace** (the `intel_dpll_mgr.c:4945` warning you cite), fired from the compositor's atomic commit. Notably this instance was a **runtime-PM / deep-idle display re-enable, not even a full s2idle suspend** — so the broken path is the warm cx0 DPLL *restore* generally, not s2idle specifically:
```
i915 [drm] DPLL 0: pll hw state mismatch
WARNING: drivers/gpu/drm/i915/display/intel_dpll_mgr.c:4945 at verify_single_dpll_state+0x177/0x650 [i915], CPU#18: kwin_wayland
 intel_dpll_state_verify+0x6d/0x220 [i915]
 intel_modeset_verify_crtc+0x4f/0x80 [i915]
 intel_atomic_commit_tail+0x954/0xd10 [i915]
 intel_atomic_commit+0x23d/0x280 [i915]
 drm_atomic_commit -> drm_mode_atomic_ioctl -> drm_ioctl -> __x64_sys_ioctl -> do_syscall_64
---[ end trace ]---
```
(Preceded on the same commit by `verify_crtc_state+0x2b3` at `intel_modeset_verify.c:225`, "pipe state doesn't match!".) Full sanitized log attached.

**Independent code-read agrees with your suspect.** Routing MTL+ through the generic DPLL manager (`1a7fad2aea74`) together with removal of the cx0-specific state verify (`ac3423721117`) leaves the resume path: `readout_dpll_hw_state()` adopting the powered-down PLL as current → `sanitize_dpll_state()` not force-disabling (the eDP PLL still claims `active_mask`) → `verify_single_dpll_state()` warn-only `memcmp` → the full cx0 reprogram never re-runs.

**Offer — re: your Ask #1 (confirm the regressing commit).** I have this exact repro hardware and can run the v6.19→v7.0 bisect you call for, and test candidate patches with fast turnaround. Plan: build/boot `1a7fad2aea74~1` (expect good) vs the series tip (expect bad) to confirm/exclude the framework commit in two boots, then narrow over `intel_cx0_phy.c` / `intel_dpll_mgr.c` if needed. Can also provide a `drm.debug=0xe` full capture and `i915_display_info` / `i915_vbt`.
