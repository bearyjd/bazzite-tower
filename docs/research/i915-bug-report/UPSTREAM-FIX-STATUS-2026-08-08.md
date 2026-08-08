# Bazzite `:stable` moved to a real 7.1.5 point release — still pre-fix. Stay pinned.

> **Status (2026-08-08):** commit `062499cc4813b5a3cbed5dd4fbe0177265858450` ("drm/i915/mtl+:
> Enable PPS before PLL", `Closes: #16042`) is still **mainline-only**. Kernel 7.2 has *still*
> not been released — no bare `v7.2` tag, no `linux-7.2.y` stable branch. Bazzite `:stable` has
> advanced to `44.20260802` / kernel **`7.1.5-ogc5.1`**, which is a genuine upstream point-release
> bump (7.1.3 → 7.1.5), unlike the previous `-ogc` rebuild. **It is still pre-fix.** There remains
> nothing to rebase or unpin to. Stay on `44.20260429` (`6.19.11-ogc1`).

## What changed since the 2026-07-23 check

- **Bazzite `:stable` bumped to `44.20260802`**, kernel `7.1.3-ogc5.1` → **`7.1.5-ogc5.1`**.
  Worth flagging: unlike the 2026-07-21 bump (an `-ogc` rebuild at an unchanged `7.1.3` base),
  **this one moves the upstream base kernel**, 7.1.3 → 7.1.5. That is exactly the shape of change
  that could carry a backport, so it was checked directly rather than assumed. It does not.
- **`linux-7.1.y` advanced `v7.1.4` → `v7.1.7`** (three point releases since the last check).
  None contain the fix.
- **7.2 is unchanged and still not shipped.** No `linux-7.2.y` head on `gregkh/linux`; no bare
  `v7.2` tag on `torvalds/linux` (only `-rcN`).

## What was checked (ground truth, reproducible)

1. **No 7.2 stable branch:**
   `git ls-remote --heads https://github.com/gregkh/linux.git 'refs/heads/linux-7.2.y'` → empty.
2. **No 7.2 final tag:**
   `git ls-remote --tags https://github.com/torvalds/linux.git 'refs/tags/v7.2*'`, filtered to
   drop `-rcN` and `^{}` → empty.
3. **7.1.y line now at v7.1.7:**
   `git ls-remote --tags https://github.com/gregkh/linux.git 'refs/tags/v7.1.*'` → `v7.1.4`,
   `v7.1.5`, `v7.1.6`, `v7.1.7`.
4. **Fix absent from the stable tags:**
   `gh api repos/gregkh/linux/compare/062499cc4813b5a3...<tag>` → `diverged` for `v7.1.7`,
   `v7.1.5` and `v7.0.14`.
5. **No backport exists at all** — this is the check that actually settles it, because a
   cherry-picked backport carries a *different* SHA and would make step 4 report `diverged`
   whether or not the fix had landed:
   - `gh api search/commits q='repo:gregkh/linux "Enable PPS before PLL"'` → **total 1**, and
     that one hit is the original `062499cc4813` (2026-06-12).
   - `gh api search/commits q='repo:gregkh/linux "062499cc4813b5a3 upstream"'` → **total 1**,
     same original commit matching its own SHA text. Stable backports are conventionally worded
     `commit <sha> upstream.`; no such commit exists.
6. **Bazzite `:stable` kernel, read without pulling the image** (`skopeo` is not installed here,
   so this went through the GHCR registry API directly):
   ```
   token:    ghcr.io/token?scope=repository:ublue-os/bazzite-nvidia:pull
   manifest: ghcr.io/v2/ublue-os/bazzite-nvidia/manifests/stable   (index -> first manifest)
   config:   ghcr.io/v2/ublue-os/bazzite-nvidia/blobs/<config digest>
   ```
   → `ostree.linux = 7.1.5-ogc5.1.fc44.x86_64`, `org.opencontainers.image.version = 44.20260802`.

   **Do not read this from a locally cached image.** The local copy of
   `ghcr.io/ublue-os/bazzite-nvidia:stable` on this machine still reported `7.1.3-ogc3.4`, two
   `:stable` releases behind, and would have produced a wrong answer that *looked* authoritative.

## Verdict

**Unchanged: stay pinned to `44.20260429` (`6.19.11-ogc1`).** Every candidate Bazzite could
rebase onto is built on a 7.0.y/7.1.y base that lacks the fix, so switching would reintroduce the
cx0 PHY-A s2idle-resume regression on this machine.

## Re-evaluation trigger (unchanged from the 2026-07-23 doc)

Re-check when either is true:

1. Kernel `7.2` reaches a final release, or a `linux-7.2.y` stable branch appears (checks 1 and 2
   above).
2. Bazzite `:stable` ships an `-ogc` kernel built from `7.2.x`, **or** from a `7.1.y`/`7.0.y`
   point release that backports `062499cc4813b5a3` — check 5 is the one that detects a backport;
   check 4 alone cannot.

When either lands, confirm the fix is genuinely present in whatever Bazzite actually ships before
proposing an unpin, and **confirm with the user before changing the base image** — see the
`CLAUDE.md` hard rule. This document is research; it changes no pin.
