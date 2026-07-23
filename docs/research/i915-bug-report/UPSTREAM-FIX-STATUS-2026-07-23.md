# Fix confirmed in mainline v7.2-rc — but v7.2 hasn't shipped, and Bazzite is still pre-fix

> **Status (2026-07-23):** commit `062499cc4813b5a3cbed5dd4fbe0177265858450` ("drm/i915/mtl+:
> Enable PPS before PLL", `Closes: #16042`) **is** an ancestor of `torvalds/linux` master and of
> `v7.2-rc1` through `v7.2-rc4` — so the fix is confirmed present in the upcoming 7.2 development
> series. However, **kernel 7.2 has not been released** (mainline is still at `-rc4`), no
> `linux-7.2.y` stable branch exists yet, and Bazzite's latest `:stable` release
> (`44.20260721`, kernel `7.1.3-ogc5.1`) is still on the pre-fix `7.1.3` base — it does not
> contain the fix. **There is nothing to rebase/unpin to yet.** Stay pinned to `44.20260429`
> (6.19.11-ogc1).

## What changed since the 2026-07-13 check

- `linux-7.1.y` advanced to `7.1.4` (tagged 2026-07-16) — still confirmed **not** containing the
  fix (`gh api repos/gregkh/linux/compare/linux-7.1.y...062499cc4813b5a3...` → `diverged`).
- Bazzite `:stable` bumped to `44.20260721` (2026-07-21) — kernel line reads
  `7.1.3-ogc3.4 ➡️ 7.1.3-ogc5.1`. This is an `-ogc` rebuild bump, **not** a new upstream point
  release; base kernel version is unchanged at `7.1.3`, still pre-fix.
- `v7.2-rc1` through `v7.2-rc4` tags now exist on `torvalds/linux` (mainline dev cycle for the
  next release).

## What was checked (git ground-truth via `gh api`, same method as prior status docs)

1. `gh api repos/gregkh/linux/compare/master...062499cc4813b5a3...` → `behind` — the fix commit
   is an ancestor of mainline `master`.
2. `gh api repos/gregkh/linux/compare/v7.2-rc1...062499cc4813b5a3...` → `behind`, and same for
   `v7.2-rc4` → `behind` — the fix is present as far back as `v7.2-rc1`, i.e. it landed before
   the 7.2 merge window closed. **Confirms the user's claim that "7.2 fixes it."**
3. `gh api repos/gregkh/linux/compare/linux-7.1.y...062499cc4813b5a3...` → `diverged` (unchanged
   from prior checks) — still absent from the 7.1.y stable line, now at 7.1.4.
4. `gh api repos/ublue-os/bazzite/releases/latest` → tag `44.20260721`, kernel line
   `7.1.3-ogc3.4 ➡️ 7.1.3-ogc5.1` — confirms Bazzite `:stable` has not moved past `7.1.3`.
5. No `linux-7.2.y` branch exists on `gregkh/linux` yet (`git ls-remote --heads` shows only
   `linux-7.0.y` and `linux-7.1.y`) — stable-ification of 7.2 hasn't started.

## Bottom line

| Tree / image | Tip as of check | Contains the fix? |
|---|---|---|
| `torvalds/linux` master | ahead of `v7.2-rc4` | **Yes** |
| `v7.2-rc1`..`v7.2-rc4` (mainline dev) | — | **Yes** |
| `linux-7.1.y` (stable) | tag `Linux 7.1.4`, 2026-07-16 | **No** |
| `linux-7.0.y` (stable) | tag `Linux 7.0.14`, 2026-06-27 | **No** |
| `ghcr.io/ublue-os/bazzite-nvidia:stable` | `44.20260721` → kernel `7.1.3-ogc5.1` | **No** |

The fix being "in 7.2" is correct but not yet actionable: 7.2 is pre-release (rc4), there's no
stable branch to track it on, and Bazzite has nothing built against it. Updating `:latest` to
`:stable` today would still ship a pre-fix kernel.

## Re-evaluation trigger (updated)

Re-check when either of these is true:
1. Kernel `7.2` reaches a final release (or `linux-7.2.y` stable branch appears) — check
   `git ls-remote --heads https://github.com/gregkh/linux.git` for `linux-7.2.y`, and/or
   `git ls-remote --tags https://github.com/torvalds/linux.git` for a bare `v7.2` tag (not
   `-rcN`).
2. `ghcr.io/ublue-os/bazzite-nvidia:stable` ships an `-ogc` kernel built from `7.2.x` or a
   `7.1.y`/`7.0.y` point release that backports `062499cc4813b5a3` — check the release
   changelog's kernel line, same method as this doc.

Once either lands (and the fix is confirmed present via `git merge-base --is-ancestor` against
whatever Bazzite actually ships), un-pin `Containerfile` back to `:stable` and drop the 6.19.x
pin note — but confirm first with the user before changing the base image (see repo `CLAUDE.md`
hard rules).
