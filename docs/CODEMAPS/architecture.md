<!-- Generated: 2026-06-14 | Files scanned: 41 | Token estimate: ~600 -->
# Architecture

**Type:** bootc OS-image repo — a declarative Fedora/Bazzite derivative. There is
no app runtime, database, or frontend; the "program" is a container image that
becomes a bootable OS.

**Base:** `ghcr.io/ublue-os/bazzite-nvidia:stable` (KDE + proprietary NVIDIA, F44+).
**Publishes:** `ghcr.io/bearyjd/bazzite-tower:{latest, latest.YYYYMMDD, YYYYMMDD, <sha>}`,
cosign-signed by digest.

## Lifecycle (source → running OS)

```
Containerfile ──FROM base────┐
system_files/ ──COPY /───────┤ build.sh  (dnf + systemctl + drop-in files)
build_files/build.sh ──RUN───┘        │
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
- `build_files/build.sh` — all image customization (265 lines)
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
