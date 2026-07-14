# Upstream fix still not in any shipped kernel — Bazzite :stable now on the 7.1.y line, but pre-fix

> **Status (2026-07-13):** the fix (commit `062499cc4813b5a3cbed5dd4fbe0177265858450`, "drm/i915/mtl+:
> Enable PPS before PLL", `Closes: #16042`) is **still not** in `linux-7.1.y` or `linux-7.0.y`. Bazzite's
> `:stable` **moved from the 7.0.y line to the 7.1.y line today** (`44.20260713`, kernel
> `7.1.3-ogc3.4`), but 7.1.3 predates the fix. Stay pinned to `44.20260429` (6.19.11-ogc1).

## What changed since the 2026-07-04 check

- Bazzite `:stable` released `44.20260713` (2026-07-13), bumping the shipped kernel from
  `7.0.9-ogc3.2` to `7.1.3-ogc3.4` — i.e. `:stable` has now crossed onto the `7.1.y` line this
  repo's re-evaluation trigger cares about. This is a real step closer to relevance, but the fix
  isn't in that kernel yet (see below), so it isn't the "both conditions met" trigger.

## What was checked (git ground-truth, not the GitLab UI — freedesktop.org still Anubis-blocked)

1. `git ls-remote --heads https://github.com/gregkh/linux.git` — only `linux-7.0.y` and
   `linux-7.1.y` exist; **no `linux-7.2.y` branch yet** (mainline is on the `v7.2-rc*` dev cycle,
   stable-ification hasn't started).
2. Fetched `linux-7.1.y` (tip `199c9959d`, tagged `Linux 7.1.3`, committed 2026-07-04 13:45 +0200)
   and `linux-7.0.y` (tip `458c6079f`, tagged `Linux 7.0.14`, 2026-06-27).
3. `git merge-base --is-ancestor 062499cc4813b5a3cbed5dd4fbe0177265858450 origin/linux-7.1.y` →
   **not an ancestor** — the fix is confirmed absent from `7.1.3`, the exact point release Bazzite
   `:stable` now ships.
4. Cross-checked via `gh api repos/gregkh/linux/compare/linux-7.1.y...062499cc4813b5a3...` →
   `diverged`, consistent with "not present."
5. `gh api repos/ublue-os/bazzite/releases/latest` confirms tag `44.20260713`
   (published 2026-07-13T06:47:03Z); WebFetch of the release notes confirms the kernel line reads
   `7.0.9-ogc3.2 ➡️ 7.1.3-ogc3.4`.

## Bottom line

| Tree | Tip as of check | Contains the fix? |
|---|---|---|
| `linux-7.1.y` (stable) | `199c9959d`, tag `Linux 7.1.3`, 2026-07-04 | **No** |
| `linux-7.0.y` (stable) | `458c6079f`, tag `Linux 7.0.14`, 2026-06-27 | **No** |
| `ghcr.io/ublue-os/bazzite-nvidia:stable` | `44.20260713` → kernel `7.1.3-ogc3.4` | **No** — pre-fix point release |

No `linux-7.2.y` stable branch exists yet for the fix to land in ahead of a mainline 7.2 release,
so the earliest realistic landing point is a future `7.1.y` point release (7.1.4+) once the
`Cc: stable@vger.kernel.org # v7.0+` tag is actually processed by the stable maintainers.

## Re-evaluation trigger (unchanged)

Re-check when either of these is true:
1. `linux-7.1.y` (or a new `linux-7.2.y`) gains a point release containing commit `062499cc4`
   (or its `28783a274e88` origin) — check via
   `git merge-base --is-ancestor 062499cc4813b5a3... origin/linux-7.1.y`.
2. `ghcr.io/ublue-os/bazzite-nvidia:stable` ships an `-ogc` kernel built from a point release that
   includes it (check the release changelog's kernel line, same method as this doc).

Once both land, un-pin `Containerfile` back to `:stable` and drop the 6.19.x pin note — but confirm
first with the user before changing the base image (see repo `CLAUDE.md` hard rules).
