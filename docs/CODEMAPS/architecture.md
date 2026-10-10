<!-- Generated: 2026-08-08 | Files scanned: 45 | Token estimate: ~650 -->
<!-- Targeted update: 2026-10-05 | Snitchwatch deployment boundary; not a full regeneration -->
# Architecture

**Type:** bootc OS-image repo — a declarative Fedora/Bazzite derivative. There is
no app runtime, database, or frontend; the "program" is a container image that
becomes a bootable OS.

**Base:** `ghcr.io/ublue-os/bazzite-nvidia-open` (KDE + NVIDIA open kernel
modules, F44+), **pinned by digest** (the source tag was `44.20260825`, kernel
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
and the same shape under `latest-kernel-*` and `snitchwatch-system-*` (opt-in system bridge), cosign-signed by digest.

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

## Application firewall boundary

`build_files/build.d/95-firewall.sh` selects the firewall and the independent
`SNITCHWATCH_BRIDGE=legacy|system` profile. Legacy remains the default: OpenSnitch
1.8.0 uses TCP `127.0.0.1:50051` and the user bridge is separately installed.
Both profiles retain `proc` and fail-open `allow`.

The opt-in system candidate consumes a pinned native build and stages units
from the same Snitchwatch source. It supplies `/usr/bin/snitchwatch-bridge-cli`,
root-only gRPC and GUI-group Unix sockets, sysusers/tmpfiles, licensing and an
immutable installed-overlay manifest. The candidate separately compiles the
reviewed OpenSnitch 1.8.0 shutdown/NFT ownership repair and records its source,
patch, binary and licenses in a daemon manifest. OpenSnitch's relative Unix address is
resolved from `/run/snitchwatch`; readiness/migration respect mutable `/etc`.
The GUI remains per user and no account receives GUI membership implicitly.

Historical October 4–5 VM artifact tests are preserved separately from fresh
image validation. Fresh native 0.1.1 reproducibility and the clean GUI build
on supported KDE 6.11 / Qt 6.11.2 passed at reconciled source `5c2b44a`.
Actual default KDE behavior and repaired-daemon shutdown/NFT behavior remain
enforcing-SELinux VM gates. See [system bridge research](../research/snitchwatch-system-bridge.md).


## Codemap index

- [image-build.md](image-build.md) — the `build.sh` pipeline (what the build does)
- [system-files.md](system-files.md) — what ships in the image (runtime surface)
- [iso-build.md](iso-build.md) — the live/installer ISO (separate `installer/` payload)
- [ci-cd.md](ci-cd.md) — workflows, promotion gate, tests
- [dependencies.md](dependencies.md) — base image, repos, packages, actions
