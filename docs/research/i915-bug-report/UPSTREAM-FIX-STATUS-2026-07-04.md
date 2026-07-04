# Upstream fix landed for #16042 — not yet in a shipped 7.0/7.1 stable kernel

> **Status (2026-07-04):** the fix is **merged into Linus's tree** but has **not** reached any
> released 7.0.y/7.1.y stable point release, and is therefore **not** in Bazzite's current
> `:stable` build. Stay pinned to `44.20260429` (6.19.11-ogc1) for now; re-check next stable
> point-release cycle.

## The fix

Git ground-truth against `torvalds/linux` (method: shallow partial clone + `git log`/`merge-base`,
same as the original 2026-06-20 research):

```
commit 062499cc4813b5a3cbed5dd4fbe0177265858450
Author: Imre Deak <imre.deak@intel.com>
Date:   Fri Jun 12 20:26:17 2026 +0300

    drm/i915/mtl+: Enable PPS before PLL

    Enabling PPS after a display port's PLL is enabled leads to PLL / DDI
    BUF timeouts during system resuming after a long (> 45 mins) suspended
    state, at least on some ARL and MTL laptops, either all or some of them
    also containing an Nvidia GPU. Enabling PPS first and then the PLL fixes
    the problem for all the reporters.

    Fixes: 1a7fad2aea74 ("drm/i915/cx0: Enable dpll framework for MTL+")
    Closes: https://gitlab.freedesktop.org/drm/i915/kernel/-/work_items/16098
    Closes: https://gitlab.freedesktop.org/drm/i915/kernel/-/work_items/16064
    Closes: https://gitlab.freedesktop.org/drm/i915/kernel/-/work_items/16042
    Cc: Mika Kahola <mika.kahola@intel.com>
    Cc: stable@vger.kernel.org # v7.0+
    Tested-by: Jouni Högander <jouni.hogander@intel.com>
    Tested-by: Marco Nenciarini <mnencia@kcore.it>
    Reviewed-by: Suraj Kandpal <suraj.kandpal@intel.com>
    (cherry picked from commit 28783a274e886dd6da61419be6020bd9d0384e9f)
```

This is exactly the patch this repo's `COMMENT.md` referenced as "`0001-drm-i915-mtl-Enable-PPS-before-PLL.patch`"
and offered a second-platform Tested-by for. It explicitly closes **#16042** (this box's tracked
issue) plus #16064/#16098 (the same regression filed by other reporters), and explicitly targets
`Fixes: 1a7fad2aea74` — the DPLL-framework commit this repo's root-cause analysis identified.
Confirms the analysis was correct and the community fix converged on it independently.

## Where it actually is (verified via git, not the GitLab UI — still Anubis-blocked)

| Tree | Tip as of check | Contains the fix? |
|---|---|---|
| `torvalds/linux` master | `dac0b8c58` — merge of `drm-fixes-2026-07-04` (now on the **v7.2-rc1** dev branch) | **Yes** — landed via that pull, i.e. merged into Linus's tree ~2026-07-04 |
| `v7.1` (final tag) | tagged 2026-06-14 | **No** — tag predates the fix commit (2026-06-16) by 2 days |
| `linux-7.1.y` (stable) | `v7.1.2`, 2026-06-27 | **No** |
| `linux-7.0.y` (stable) | `v7.0.14`, 2026-06-27 | **No** — predates the mainline merge |
| `ghcr.io/ublue-os/bazzite-nvidia:stable` | `44.20260629` → kernel `7.0.9-ogc3.2` | **No** — ships a 7.0.x line kernel from before the fix existed anywhere upstream |

Despite the `Cc: stable@vger.kernel.org # v7.0+` tag, the fix only reached Linus's tree today;
stable backports to `7.0.y`/`7.1.y` normally trail mainline by roughly one point-release cycle
(1-2 weeks), and ublue's `-ogc` builds trail *that*. So there is currently no installable kernel —
official or `-ogc` — that carries this fix.

## Re-evaluation trigger

Re-check when either of these is true:
1. `linux-7.0.y` or `linux-7.1.y` gains a point release containing commit `062499cc4` (or its
   `28783a274e88` origin) — check via `git log --oneline <tag> | grep -i "pps before pll"`.
2. `ghcr.io/ublue-os/bazzite-nvidia:stable` ships an `-ogc` kernel built from a 7.0.x/7.1.x point
   release that includes it (check the release changelog's kernel line, same method as this doc).

Once both land, un-pin `Containerfile` back to `:stable` and drop the 6.19.x pin note.
