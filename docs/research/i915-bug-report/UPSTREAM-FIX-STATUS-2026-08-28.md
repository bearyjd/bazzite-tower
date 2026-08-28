# Fix shipped in kernel 7.2 — but `bazzite-nvidia` forked off the branch that carries it. Stay pinned.

> **Status (2026-08-28):** commit `062499cc4813b5a3` ("drm/i915/mtl+: Enable PPS before PLL",
> `Closes: #16042`) **has shipped** — `v7.2` is now a final tag on `torvalds/linux`, and
> `linux-7.2.y` is a real stable branch (currently at `v7.2.2`), both containing the fix. This
> fires re-evaluation trigger #1 from the 2026-08-08 doc. **It does not change the pin.**
> `bazzite-nvidia:stable` — the proprietary-driver variant this repo bases on — no longer tracks
> the 7.x line at all: as of build `44.20260820` it was forked onto a separate `ogc-lts` kernel
> flavor pinned to `6.18.44-ogc1.1.fc44`, which predates the regression and does not carry the
> fix (nor the bug). Only `bazzite-nvidia-open:stable` followed the main flavor to `7.2.0-ogc6.1`
> and actually has the fix — but that's the open-driver variant this repo's Containerfile comment
> already ruled out for known Optimus suspend/resume gaps.

## What changed since the 2026-08-08 check

- **Kernel 7.2 is out.** `git ls-remote --tags torvalds/linux 'refs/tags/v7.2*'` now returns a
  bare `v7.2` tag (previously only `-rc` tags existed). `linux-7.2.y` exists as a stable branch
  on `gregkh/linux`, currently tagged through `v7.2.2`.
- **The fix is confirmed in both.** `gh api repos/torvalds/linux/compare/062499cc4813b5a3...v7.2`
  → `ahead` (base is an ancestor of head — fix included). Same check against the `linux-7.2.y`
  branch head → `ahead`.
- **The fix is still absent from every 7.0.y/7.1.y point release.**
  `gh api repos/gregkh/linux/compare/062499cc4813b5a3...v7.1.12` (latest 7.1.y tag) → `diverged`.
  No backport exists either (`gh api search/commits q='repo:gregkh/linux "062499cc4813b5a3
  upstream"'` → total 1, still just the original commit — same null result as every prior check).
- **`bazzite-nvidia` and `bazzite-gnome-nvidia` split off the main kernel track.** Upstream commit
  `3eb7b0940a8f2fc8e15054d25cad2d3a979850ff` ("feat(nvidia): Use LTS kernel for LTS Nvidia
  Driver", in release `44.20260820`) adds a dedicated CI matrix entry:
  `kernel_flavor: ogc-lts`, `kernel_version: 6.18.44-ogc1.1.fc44`, for exactly these two images.
  The *main* flavor (plain `bazzite`, and `bazzite-nvidia-open`) went to `7.2.0-ogc6.1` instead.
- **Verified `bazzite-nvidia:stable` actually ships the LTS kernel, not just the matrix entry:**
  read via GHCR registry API (no image pull) → `ostree.linux = 6.18.44-ogc1.1.fc44.x86_64`,
  `org.opencontainers.image.version = 44.20260825`.
- **Verified 6.18.44 does not carry the DPLL-framework regression either** (same check-5
  methodology the repo already uses, applied to the *regression's* lead commit instead of the
  fix): `gh api repos/gregkh/linux/compare/1a7fad2aea74...v6.18.44` → `diverged`. So
  `bazzite-nvidia:stable`'s new LTS track is, like 6.19.11, in the "before the regression existed"
  bucket — not fixed, just never broken.
- **Verified `bazzite-nvidia-open:stable` did move to the fixed kernel:** registry read →
  `ostree.linux = 7.2.0-ogc6.1.fc44.x86_64`, same `44.20260825` release. This is the only
  candidate today that is both (a) an upstream-published Bazzite tag and (b) genuinely running a
  kernel with `062499cc4813b5a3` in it.

## What was checked (ground truth, reproducible)

1. `v7.2` final tag exists: `git ls-remote --tags torvalds/linux 'refs/tags/v7.2*'`, filtered to
   drop `-rcN`/`^{}` → `v7.2`.
2. `linux-7.2.y` stable branch exists: `git ls-remote --heads gregkh/linux
   'refs/heads/linux-7.2.y'` → present, and tags `v7.2.1`, `v7.2.2` exist on the same repo.
3. Fix ancestry into both: `gh api repos/torvalds/linux/compare/062499cc4813b5a3...v7.2` and
   `gh api repos/gregkh/linux/compare/062499cc4813b5a3...<linux-7.2.y HEAD sha>` → both `ahead`.
4. Fix still absent from 7.1.y: `gh api repos/gregkh/linux/compare/062499cc4813b5a3...v7.1.12`
   → `diverged`; no cherry-picked backport found via commit-message search.
5. `bazzite-nvidia`/`bazzite-gnome-nvidia` fork onto `ogc-lts`/6.18.44:
   `gh api repos/ublue-os/bazzite/commits/3eb7b0940a8f2fc8e15054d25cad2d3a979850ff` — diff to
   `.github/workflows/build.yml` adds the two images at `kernel_flavor: ogc-lts`,
   `kernel_version: 6.18.44-ogc1.1.fc44`, while the release notes for `44.20260820`/`44.20260825`
   show the *main* flavor's kernel column advancing `6.17.7-ba29 → 7.2.0-ogc4.1 → 7.2.0-ogc6.1`.
6. `bazzite-nvidia:stable` current kernel, read without pulling the image (GHCR registry API,
   `Accept: application/vnd.oci.image.manifest.v1+json` then the config blob) →
   `ostree.linux = 6.18.44-ogc1.1.fc44.x86_64`, `org.opencontainers.image.version = 44.20260825`.
7. Regression absent from 6.18.44: `gh api repos/gregkh/linux/compare/1a7fad2aea74...v6.18.44`
   → `diverged` (the DPLL-framework series is not an ancestor of `v6.18.44`).
8. `bazzite-nvidia-open:stable` current kernel, same registry method →
   `ostree.linux = 7.2.0-ogc6.1.fc44.x86_64`, `org.opencontainers.image.version = 44.20260825`.

## Verdict

**Unchanged: stay pinned to `44.20260429` (`6.19.11-ogc1`).** Trigger #1 from the 2026-08-08 doc
fired (7.2 final + stable branch, both containing the fix), but trigger #2 has not, and may now
never fire the way it was originally framed — `bazzite-nvidia:stable`, the proprietary-driver
image this Containerfile bases on, has moved to a kernel line (`ogc-lts`/6.18.x) that upstream
appears to intend to keep separate from the fixed 7.2.x main line, not just be a few point
releases behind it. Nothing published as `bazzite-nvidia:*` carries the fix today.

Two things are now worth the user's attention that weren't true on 2026-08-08 (research only —
no pin change without explicit confirmation, per `CLAUDE.md`):

1. **`bazzite-nvidia`'s new `ogc-lts` 6.18.44 track is a second confirmed-unaffected kernel**,
   independent of 6.19.x. Worth watching whether it's a maintained rolling LTS line (would be a
   defensible future default alongside 6.19.11) or a one-off pin upstream might abandon.
2. **`bazzite-nvidia-open:stable` is, as of today, the only upstream tag with the actual fix
   applied.** Whether its previously-documented Optimus suspend/resume gaps
   (`Containerfile` lines 17-19) are still accurate in 2026 is now a live question worth
   re-checking — it's the only path to "on the fixed kernel and still upstream-tracked," not a
   host-side override.

## Re-evaluation trigger (revised)

Re-check when either is true:

1. `bazzite-nvidia:stable` (the proprietary variant) ships a kernel with `062499cc4813b5a3` in
   it — whether that's `ogc-lts` picking up a 7.x-derived backport, or the flavor split being
   reversed. Use check 7's methodology (ancestry of the *fix* commit, not just a version-number
   read) since the flavor split means version numbers alone no longer imply what's inside.
2. Someone re-verifies whether `bazzite-nvidia-open`'s Optimus suspend/resume gaps are still real
   on this hardware class — that would make the `-open` variant a candidate today, without
   waiting on trigger 1 at all.

When either lands, confirm the fix is genuinely present in whatever Bazzite actually ships before
proposing an unpin, and **confirm with the user before changing the base image** — see the
`CLAUDE.md` hard rule. This document is research; it changes no pin.
