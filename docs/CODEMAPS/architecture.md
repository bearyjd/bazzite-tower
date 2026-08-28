<!-- Generated: 2026-08-08 | Files scanned: 45 | Token estimate: ~650 -->
# Architecture

**Type:** bootc OS-image repo — a declarative Fedora/Bazzite derivative. There is
no app runtime, database, or frontend; the "program" is a container image that
becomes a bootable OS.

**Base:** `ghcr.io/ublue-os/bazzite-nvidia-open` (KDE + NVIDIA open kernel
modules, F44+), **pinned to a dated tag** (`44.20260825`, kernel
`7.2.0-ogc6.1`) as the default. This carries the i915 Meteor Lake s2idle-resume
fix (kernel 7.2) — proprietary `bazzite-nvidia` was used until 2026-08-28 but
forked onto a kernel track (`ogc-lts`, 6.18.x) that won't receive it; see
`docs/research/i915-bug-report/UPSTREAM-FIX-STATUS-2026-08-28.md`. Pinned
rather than `:stable` for reproducibility, same as every base pin here.
`bazzite-nvidia-open:stable` is still built, as the opt-in `:latest-kernel` tag
(`BASE_IMAGE=...:stable` build-arg) — an early-warning channel, not a
recommendation to boot it. See the `Containerfile` header comment for the
full history.
**Publishes:** `ghcr.io/bearyjd/bazzite-tower:{latest, latest.YYYYMMDD, YYYYMMDD, <sha>}`
and the same shape under `latest-kernel-*`, cosign-signed by digest.

## Lifecycle (source → running OS)

```
Containerfile ──FROM base────┐
system_files/ ──COPY /───────┤ build.sh  (dnf + systemctl + drop-in files)
build_files/build.d/ ─RUN────┘        │
                                      ▼
                            bootc container lint
                                      │   CI: smoke gate → push GHCR → cosign sign (by digest)
                                      ▼
                   ghcr.io/bearyjd/bazzite-tower:latest ──┐
                                      │                    │ installer/ payload + titanoboa
                  bootc switch /      │                    ▼
                  weekly rebase       │          live/installer ISO  (Secure Boot OK)
                                      ▼
                          laptop OS (ThinkPad P1)
```

## Entry points

- `Containerfile` — build entry: FROM base → COPY system_files → RUN build.sh → lint
- `build_files/build.sh` — thin runner; executes `build_files/build.d/*.sh` in filename order
- `build_files/build.d/` — all image customization, one concern per script (13 scripts)
- `system_files/` — static content baked verbatim into the image (units, recipes, kargs, helpers)
- `installer/` — separate payload builder for the live/installer ISO (titanoboa input)
- `Justfile` — local build / VM / test recipes
- `.github/workflows/build.yml` — CI build + gate + push + sign

## Codemap index

- [image-build.md](image-build.md) — the `build.sh` pipeline (what the build does)
- [system-files.md](system-files.md) — what ships in the image (runtime surface)
- [iso-build.md](iso-build.md) — the live/installer ISO (separate `installer/` payload)
- [ci-cd.md](ci-cd.md) — workflows, promotion gate, tests
- [dependencies.md](dependencies.md) — base image, repos, packages, actions
