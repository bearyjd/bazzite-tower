# Release playbook (snitchwatch-system image)

How a release gets from merged code to the laptop. Proven end to end on r14
(2026-10-13). The mechanics of each gate live in `docs/r13-publication.md`; this
file is the order of operations and the testing method. The release promoter is
`release.yml` (**Release Snitchwatch System**). Candidate workflow, control
variable, branch and GHCR package names retain their historical `r13` identities:
they name the pipeline, so reuse them across rounds. Only the Snitchwatch GUI
candidate target is per round
(`r14-candidate` …), because its fixed-name publish refuses to replace an existing
release.

**Scope:** this applies only when the owner has explicitly authorized release
work. Otherwise the `AGENTS.md` rule stands: no push, merge or PR without an
explicit owner instruction.

## Base refresh versus feature acceptance

`base-refresh.yml` runs daily and by dispatch on `main`, without a per-base owner
approval. `.github/base-refresh-policy.json` pins accepted product source
`acd26cef83a4df9884c00f7b834a5d6e60a3206f`: legacy publishes `:stable` and its
compatibility alias `:latest` (`IMAGE_TAG=latest`); reviewed r16.1 system publishes
only `:snitchwatch-system`. Both profiles use this exact product source.
The workflow resolves a fresh immutable Bazzite base, enforces the
`44.20261023` / `7.2.9-ogc3.1.fc44` floor and rejects stale policy, then requires
build/test/signing/SPDX/provenance/host-policy gates before publication.

The r16.1 refresh-policy transition must merge only after protected promotion
completes successfully, independent destination signature/SPDX checks pass, and
the exact policy PR head has green CI and independent review. Its sole system
release-baseline exception is `sha256:3f9a0d238e69917a33a23710a53963e70b64526cc8e44f9d24787944dcca1f27`,
verified with the repository key before use. The prior r15.1 digest receives no
exception. Normal destination-bound signed refresh provenance still enforces
monotonic Bazzite/kernel versions. A missing system tag always fails closed.
Host runtime acceptance remains unverified; the owner authorized this CI-based
release path. This does not establish runtime or host eBPF compatibility.

The missing-`:stable` recovery is authorized only for server-assigned workflow
run 2, attempt 1, on the exact `main` workflow identity. Run 1
(`40062419690`, head `d8407c60f74ceafbcbc2e333198c9fd23ce4e260`) failed during
installed-policy collection after the legacy build, smoke and boot gates; its
publisher jobs never ran. The reviewed policy records this exact failed run/head.
Before allowing missing `:stable`, the publisher reads GitHub's run metadata and
attempt-1 jobs API and requires completed failure, run 1/attempt 1, the expected
repository/workflow/main/head, a complete job list, and both publishers skipped.
API errors or any mismatch fail closed. Other run numbers, reruns, and later
missing canonical tags fail closed; they cannot reset the signed version
baseline. If this bounded recovery fails before establishing `:stable`, another
independently reviewed recovery change is required. Do not delete the canonical
tag or widen the missing-tag exception to recover.

Installed trust validation distinguishes immutable staging intent from the active
policy merge performed by the accepted installer. The staged policy/key/registry
and active key/registry retain exact hash checks. Active policy requires reject
by default and the exact repository key and `matchRepository` rule; more-specific
tower tag/digest/child scopes are refused. Its full bytes and hash are recorded,
bound by provenance, and independently re-extracted by the credential-free
validator; unrelated upstream policy rules remain intact.

The release sequence below remains the feature-acceptance path. A newly accepted
feature digest still needs owner runtime acceptance and the protected `release`
environment promoter. Only a subsequent independently reviewed policy update may
make that product the source of automatic refreshes. A weekly base-bump PR
updates the candidate `Containerfile` pin; merging it does not accept features.

Automatic refresh updates registry tags sequentially. `stable` and `latest` are verified to resolve to the same digest on success, but the registry has no transaction spanning both tags. A partial transport failure reports attempted tags, confirmed tags and the expected digest in the run summary; an attempted write may have completed despite a lost response. Rerun the refresh workflow with the same reviewed policy to verify the signed baseline and repair the aliases.

## Roles

- **Primary agent**: one orchestrator. Plans, sets repository variables, pushes
  candidate refs, merges PRs that pass the merge rule, dispatches workflows.
- **Executor sub-agents**: write each change in its own worktree and open a PR.
- **Independent reviewers**: separate read-only sub-agents (opus for release and
  security review) that
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
   `python3 tests/test-snitchwatch-image-build-daemon.py`, `tests/check-supply-chain.sh`,
   `python3 tests/test-snitchwatch-system.py`, `python3 tests/test-snitchwatch-gate.py`.
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
3. **GUI candidate** (Snitchwatch). First, for a new round, a Snitchwatch PR renames
   the hard-coded target in `.github/workflows/candidate-gui.yml`, `ci.yml` and
   `packaging/flatpak/build-candidate.py` (branch, tag, release, packet target,
   concurrency group, `R<NN>_CANDIDATE_REVIEWED_SHA`) to `r<NN>`; review, merge, and
   re-pin the tower to that commit (step 2). Pushing `r15-candidate` while the
   workflow still says `r14-candidate` triggers nothing. Then confirm green CI on main at the source commit and
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
8. **Promoter PR**: change only `env.DIGEST` (the image digest) and
   `--source-digest` (the candidate head *commit* SHA, not an image digest) in
   `release.yml` (+ tests/docs). Review, CI, merge. Merging arms; it does not
   publish.
9. **Owner acceptance** (below), then dispatch
   `gh workflow run release.yml -R bearyjd/bazzite-tower --ref main -f digest=<digest>`; the owner
   approves the `release` deployment. Then publish the stable GUI release as a byte
   copy of the candidate prerelease assets, with release notes.

If a candidate run fails after the gate, the fix needs a new reviewed head and a
new identity/control commit (the gate requires run attempt 1). Re-pinning or a
workflow fix changes `workflowSha256`/pins hashes, so redo steps 4-6.

## Owner runtime acceptance (laptop)

The host policy (`default: reject`) does not list the candidate registry, and
`bootc switch` without `--enforce-container-sigpolicy` does not apply it. So the
owner verifies the signature with cosign first, and only after it passes switches
by that exact digest (the digest binds the content to what was verified):

```
cosign verify --key /etc/pki/containers/bazzite-tower-cosign.pub \
  ghcr.io/bearyjd/bazzite-tower-r13-candidate@<digest> >/dev/null && echo SIGNATURE OK
sudo bootc switch ghcr.io/bearyjd/bazzite-tower-r13-candidate@<digest>
sudo reboot
```

The switch may already be staged (pending deployment) by the time a session
starts; check `bootc status` before asking the owner to repeat it. Then only the
reboot is left.

Checks after boot: `bootc status --booted` shows the digest; `systemctl status
opensnitch.service snitchwatch-system-bridge.service` active (the unit is
`opensnitch`, not `opensnitchd`); the GUI features in the release work (tree, rule
button, `app:`/`country:` filters, side panel, map); web, DNS and Tailscale work.
Failure: `sudo bootc rollback && sudo reboot`, then report what broke. After promotion: `sudo bootc switch --enforce-container-sigpolicy
ghcr.io/bearyjd/bazzite-tower:snitchwatch-system` (same digest, nothing to download).

## Firewall defaults and owner checks

The original r15 system release used `DefaultAction deny`; r15.1 and subsequent
products retain `DefaultAction allow` in both profiles with `ProcMonitorMethod proc`. Deny
enforcement requires explicit operator opt-in. The system profile uses a root-only
`unix:opensnitchd.sock`. Extra owner acceptance steps:

- Before the reboot: `sudo ostree admin config-diff | grep opensnitchd` (copy the
  `/usr/etc` version over only after reviewing local changes), and retain a console
  during any explicit deny-policy experiment.
- `sudo usermod -aG snitchwatch-ui <user>` so the GUI can reach the socket.
- After boot: `ss -xl` shows the socket root-owned `0600`, nothing listens on
  `127.0.0.1:50051`, the daemon log is clean, and OS updates still work (the always-on
  `000-snitchwatch-tower-os-update` and `-resolved-dns` rules).
- Enable the recommended rules in the GUI (e.g. chrony NTP). Tower-shipped rules use
  the reserved `000-snitchwatch-` prefix, so the bridge treats them as read-only;
  RUNBOOK has the restore command.

Cut rule: a round is Snitchwatch `main` as merged when the previous round is promoted.
At the cut, re-pin to that head (also `git grep 000-snitchwatch-tower <sha>` for a
name collision), set the new `R<NN>_CANDIDATE_REVIEWED_SHA`, review, merge, then run
the pipeline above. Packaging, daemon-config, unit or socket changes after the cut
go to the next round.

## Lessons from r14

- Fixed-name publication refuses replacement: give the GUI candidate per-round names.
- `actions/attest-build-provenance` puts certificate provenance on the image, so
  `cosign verify-attestation --key` must use `--new-bundle-format` (fixed in #113);
  plain `cosign verify --key` is unaffected.
- A boot test skipped by path filters can be covered by an earlier head with the same
  image inputs; say so in the evidence.
- Set repository variables before pushing a trigger ref, every time.
- Host service UIDs differ from upstream assumptions (`systemd-resolved` 990, chrony
  994, avahi 70). A rule that pins a numeric UID can silently never match and fail
  closed; resolve the UID from the account name at run time. Treat a known
  non-matching DNS opt-in as a recorded issue, not a regression, unless the round
  was meant to fix it.
