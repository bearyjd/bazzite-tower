# Release playbook (snitchwatch-system image)

How a release gets from merged code to the laptop. Proven end to end on r14
(2026-10-13). The mechanics of each gate live in `docs/r13-publication.md`; this
file is the order of operations and the testing method. The workflow, variable,
branch and GHCR package names still say `r13`: they name the pipeline, not the
round, so reuse them. Only the Snitchwatch GUI candidate target is per round
(`r14-candidate` …), because its fixed-name publish refuses to replace an existing
release.

## Roles

- **Primary agent**: one orchestrator. Plans, sets repository variables, pushes
  candidate refs, merges PRs that pass the merge rule, dispatches workflows.
- **Executor sub-agents**: write each change in its own worktree and open a PR.
- **Independent reviewers**: separate read-only sub-agents (strongest model) that
  never authored the change. Every PR, every candidate identity and every control
  commit gets one. First line of output is `VERDICT: PASS` or `VERDICT: FAIL`.
- **Owner**: runtime acceptance on the laptop (sudo + reboot) and the approval click
  on the `release` environment. Agents never do either.

Merge rule: merge only when CI is green on the exact PR head AND an independent
review reports no CRITICAL/HIGH. Use `gh pr merge --merge --match-head-commit <sha>`.
Never force-push (including `--force-with-lease`); to update a branch, merge main in.

## Testing method (cheapest first, every step evidenced)

1. Local offline gates in the worktree: `just lint`, `just check`,
   `python3 tests/test-r13-publication.py`, `tests/test-snitchwatch-system.sh`,
   `tests/test-snitchwatch-toolchain.sh`, `python3 tests/test-snitchwatch-image-build.py`,
   `python3 tests/test-snitchwatch-image-build-daemon.py`, `tests/check-supply-chain.sh`.
   No `just build` or podman builds; CI builds the images.
2. PR CI on the exact head: Build + Verify for all three variants (`safe-pin`,
   `latest-kernel`, `snitchwatch-system`), plus the boot test when it is not
   path-filtered. A merge commit's CI does not count; the candidate head is the PR head.
3. Independent review of the PR (see Roles). Reviewers recompute hashes themselves;
   they do not trust the author's numbers.
4. Recompute every pinned value from git bytes, never by hand:
   `git rev-parse <sha>^{tree}`, `sha256` of `git show <sha>:<path>`, and the
   Snitchwatch submodule gitlink with `git ls-tree`.
5. Run the real gate code locally with a simulated Actions environment before any
   remote effect, and confirm a wrong head is refused (a merge commit with the same
   tree must fail on commit identity).
6. Watch CI in the background (`gh run watch … --exit-status` or an until-loop on
   `gh run view --json status`); read only `--log-failed | grep` excerpts.
7. Runtime acceptance by the owner on the laptop (below). Nothing is promoted first.

## Order of operations

1. **Source**: Snitchwatch changes merge to its `main` with green CI + review.
2. **Pin**: tower PR updates `build_files/firewall/snitchwatch-system-pins.json`
   (`sourceCommit`, `sourceTree`; every `sourceFiles` hash re-checked) and
   `EXPECTED_PINS_SHA256` in `snitchwatch-system-verify.py`, plus doc mentions.
   Gates, review, merge.
3. **GUI candidate** (Snitchwatch): confirm green CI on main at the source commit and
   that the round's branch/tag/release do not exist. Set
   `R<NN>_CANDIDATE_REVIEWED_SHA` FIRST, then push the source commit to the
   `r<NN>-candidate` branch. Pushing an identical head emits no event, so ordering
   matters: a push before the variable only runs the hold job.
4. **Candidate identity**: compute the eight identity fields from the tower candidate
   head (PR head with green PR CI). Independent workflow review of that identity.
5. **Control commit**: on top of the candidate head, write `.github/r13/authority.json`,
   `receipts/<sha>.json` and `evidence/<sha>.json` with `ci/r13-publication.py`'s
   `canonical()` bytes (script it; never hand-edit). `accepted` stays null. Run the
   approval gate locally, push to a new `ci/r<NN>-primary-control-<date>` branch,
   then a second independent review of the control commit.
6. **Arm and fire**: set `R13_CONTROL_HEAD`, then `R13_CANDIDATE_HEAD`, then
   fast-forward `ci/r13-candidate-hold-promotion` to the candidate head (check
   `git merge-base --is-ancestor` first; never delete/recreate the branch).
7. **Record**: digest from the `r13-candidate-<run>` tag in GHCR, cross-checked
   against the run log.
8. **Promoter PR**: change only `env.DIGEST` and `--source-digest` in
   `r13-promote.yml` (+ tests/docs). Review, CI, merge. Merging arms; it does not
   publish.
9. **Owner acceptance** (below), then dispatch
   `gh workflow run r13-promote.yml --ref main -f digest=<digest>`; the owner
   approves the `release` deployment. Then publish the stable GUI release as a byte
   copy of the candidate prerelease assets, with release notes.

If a candidate run fails after the gate, the fix needs a new reviewed head and a
new identity/control commit (the gate requires run attempt 1). Re-pinning or a
workflow fix changes `workflowSha256`/pins hashes, so redo steps 4-6.

## Owner runtime acceptance (laptop)

The host policy rejects unlisted registries, so the candidate is switched to by
digest after an explicit signature check:

```
cosign verify --key /etc/pki/containers/bazzite-tower-cosign.pub \
  ghcr.io/bearyjd/bazzite-tower-r13-candidate@<digest> >/dev/null && echo SIGNATURE OK
sudo bootc switch ghcr.io/bearyjd/bazzite-tower-r13-candidate@<digest>
sudo reboot
```

Checks after boot: `bootc status --booted` shows the digest; `systemctl status
opensnitchd snitchwatch-system-bridge.service` active; the GUI features in the
release work; web, DNS and Tailscale work. Failure: `sudo bootc rollback && sudo
reboot`. After promotion: `sudo bootc switch --enforce-container-sigpolicy
ghcr.io/bearyjd/bazzite-tower:snitchwatch-system` (same digest, nothing to download).

## Lessons from r14

- Fixed-name publication refuses replacement: give the GUI candidate per-round names.
- `actions/attest-build-provenance` puts certificate provenance on the image; key
  verification must use `cosign … --new-bundle-format` (fixed in #113).
- A boot test skipped by path filters can be covered by an earlier head with the same
  image inputs; say so in the evidence.
- Set repository variables before pushing a trigger ref, every time.
