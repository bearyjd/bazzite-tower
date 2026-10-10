# AGENTS.md — bazzite-tower (for Codex and other coding agents)

A bootc OS-image repo (Fedora/Bazzite derivative) for ONE machine: a ThinkPad P1 (Meteor Lake, NVIDIA Optimus).
No app runtime. The "program" is the image: `Containerfile` + `build_files/` + `system_files/`.
Full detail lives in `CLAUDE.md` (same rules, longer), `README.md`, `docs/RUNBOOK.md`, `docs/CODEMAPS/`. Read those on demand, not up front.

## Start here
1. If `.omc/STATE.md` exists in your checkout, read it first (<=60 lines). It is local and gitignored. Never read `.omc/HANDOFF.md` whole; it is a long log.
2. State your task in one sentence, then read only the files it needs, with line ranges.
3. Finish by writing a short result file (what changed, what you ran with PASS/FAIL, what is unverified). Do not paste logs.

## Commands (from `Justfile`; do not invent others)
- `just lint` (shellcheck over all `*.sh`; may fail on root-owned dirs under `output/`, so run `shellcheck <file>` on what you touched)
- `just check` (Justfile syntax), `just lint-containerfile` (hadolint, seconds)
- `just smoke` (asserts a BUILT image; the CI gate) — needs `just build` first
- `just test-snitchwatch-daemon-patch` (patch + Go/C tests in throwaway containers, minutes, needs network)
- Offline tests: `bash tests/test-snitchwatch-toolchain.sh`, `python3 tests/test-snitchwatch-image-build-daemon.py`, `python3 tests/test-snitchwatch-image-build.py`, `tests/test-snitchwatch-system.sh`, `tests/check-supply-chain.sh`
- Do NOT run `just build*`, `podman build`, or VM/ISO builds unless the task truly requires them. Prefer static checks, then CI.

## Hard rules
- No `git push`, merge, force-push, or PR creation without the owner's explicit instruction. Never `rm -rf`.
- Never commit, stage or read `cosign.key`; never remove it from `.gitignore`.
- Never change the `Containerfile` base-image pin without re-reading `docs/research/i915-mtl-resume-2026-06-20.md`.
- Never weaken or delete an assertion in `tests/smoke.sh` or `tests/boot-check.sh`; update it to the new intent and say so.
- Hardware tuning (`kargs.d/*.toml`, RAS/thermal/audio in `build_files/build.d/`, RUNBOOK "verified" facts) changes only with an owner instruction or evidence from THIS machine (journalctl/smartctl/ras-mc-ctl). Otherwise ask for it.
- `build_files/firewall/snitchwatch-system-install-deps.sh` must stay byte-identical to the upstream Snitchwatch copy (`cmp` in `snitchwatch-system-build.sh`).
- The pinned toolchain RPMs live in `build_files/firewall/snitchwatch-system-daemon-pins.json`; if you change that file, update `EXPECTED_PINS_SHA256` in `snitchwatch-system-daemon-verify.py` and rerun the tests. Use only the signed Koji copies (`.../data/signed/6d9f90a6/...`).
- Update the relevant `docs/CODEMAPS/*` in the same change as the code. Conventional-commit subjects (`feat:`, `fix:`, `ci:`, `chore:`, `docs:`).

## Token discipline (measured: cost = context size x turns)
- One task per thread. Stop near ~300 turns or ~250k context; write a new `.omc/STATE.md` and start fresh.
- Low reasoning effort for mechanical edits; save high effort for design and review.
- One orchestrator; sub-agents get a written spec, a turn cap (~150) and an output cap. Peers exchange files or PR links, not commentary.
- Use scripts for repeatable work and read only the short result or first error (e.g. `gh run view --log-failed | grep -m5 -i error`), never whole logs.
- Do not re-read files already in context; no polling loops. Run long jobs in the background and wait for completion.
- Heavy runs go across days: check the weekly rate limit before launching a big job.
