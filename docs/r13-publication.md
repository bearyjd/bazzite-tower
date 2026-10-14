# r13 candidate and accepted-digest publication

The system stable tag is held. `build.yml` still tests the system profile on PRs,
but push, schedule and ordinary dispatch publish only legacy `safe-pin` and
`latest-kernel`. A post-merge build cannot move `:snitchwatch-system`. Only
`r13-promote.yml` can move that tag: a minimal dispatch-only promoter for one
fixed, already-built digest (see "Fixed-digest promotion" below). Runtime
evidence and review for that digest are recorded in the PR and release notes,
not in workflow variables.

## Reviewed workflow and control trust

The primary sets repository variables only after retrieving native independent
review evidence and exact-head CI, never from candidate-authored PASS data:

| Variable | Meaning |
|---|---|
| `R13_CANDIDATE_HEAD` | Exact independently reviewed tower candidate commit |
| `R13_CONTROL_HEAD` | Exact independently reviewed commit holding gate code, primary authority, packets and receipts |
| `R13_ACCEPTED_PACKET_SHA256` | Unused: only the retired packet/receipt promoter read it |

All heads are full lowercase 40-character Git commits. The control commit can
live on a separate control branch; no candidate main merge is required. Control
data names are hash-derived, never paths from dispatch/artifact input. Gate
jobs check out only that exact control commit with `contents: read` and no
persisted credentials. Privileged jobs check out no repository, execute no
candidate code/image, and use pinned actions plus fixed inline data operations.

The initial `.github/r13/authority.json` has null candidate and acceptance
entries. It deliberately authorizes nothing. The primary must verify the actual
independent reviewer's identity/evidence, record its hash and exact bindings in
the control store, and then review the control commit. Receipt `reviewer` and
`verdict` fields alone establish no trust. The externally configured reviewed
control commit is the trust root; candidate artifacts cannot add receipts there.
Repository writers/configuration administrators remain trusted, as with the
existing release workflows. This is not an external approval service.

## Candidate flow

Push only the exact reviewed head on `ci/r13-candidate-hold-promotion` after the
primary records the allowlisted head/control commit. This push trigger works
before the workflow is merged to main; a new workflow-dispatch-only file does
not. Optional manual dispatch becomes available when the workflow exists on the
default branch. Unset/stale variables leave the publication jobs held. First
attempt only: a rerun requires a new independently recorded identity.

The fixed candidate branch already published an unarmed draft head; pushing
that identical head again emits no new push event. Bootstrap the final head as
follows, with all authority still held during source review:

1. Finish the source pin, workflow changes and independent exact-head reviews.
   Push the final tower head on a separate review branch and open/update its
   read-only PR against main. This is primary work, not automatic publication.
2. Require fresh green `build.yml` PR verification for that exact head, including
   all three image variants. PR checkout explicitly uses
   `github.event.pull_request.head.sha || github.sha`, proves actual Git HEAD,
   and records the checked-out head/tree. A synthetic merge checkout or older
   run cannot count. When counted, boot-test uses the same actual-head proof
   and a read-only build job; its issue notifier cannot run on PRs.
3. Record trusted native review/CI evidence and the independently reviewed
   immutable control commit. Only the primary may then set the reviewed head
   and control variables. Stable promotion variables remain absent until real
   accepted-artifact runtime receipts exist.
4. Fast-forward the existing fixed candidate branch to that exact tested and
   reviewed head. The head change emits a new push event and enables the
   candidate build/publication flow. Do not force-push, delete/recreate the
   branch, or merge release-affecting code to main for registration. If it is
   already at the reviewed head, hold and wait for a necessary reviewed successor;
   do not manufacture a push event or treat the skipped run as artifact CI.

The ordinary verify job is PR-only; dispatching `build.yml` on a detached review
head does not run it. Workflow-dispatch registration on main is not a bootstrap
prerequisite. This procedure does not configure variables or claim remote green
CI itself. Refresh the old stable-writer inventory before eventual promotion;
the writer lock and refusal remain enforced.

The credential-free builder checks clean commit/tree/workflow/pin identities,
builds the system profile in OCI format, executes the unchanged smoke and boot
assertions, collects the installed bridge/daemon manifests and actual binary
hashes, and queries the installed RPM database into the existing SPDX generator.
The OCI export uses `--all --preserve-digests`. A format conversion failure is
HOLD. This one-machine image accepts one linux/amd64 manifest; an unexpected
multi-platform index is HOLD rather than an implicit platform selection.

`r13-image-data` contains exactly `image.oci.tar`, `candidate.json`, `sbom.json`,
`build-provenance.json`, `bridge-manifest.json`, `daemon-manifest.json`,
`cosign.pub`, `policy.json` and `bazzite-tower.yaml`. The read-only validator
checks the complete file inventory, hashes, safe OCI entries and every layer,
installed/source identities and unchanged public key/stable trust policy.
Publisher credentials exist only in a separate fresh job after validation.

The fixed candidate package is `ghcr.io/bearyjd/bazzite-tower-r13-candidate`.
Its only pointers are `r13-candidate-<run>` and `r13-candidate`. No target is an
input. Automated CI uses the existing pinned cosign installer, signing secret,
SPDX and GitHub build provenance machinery. A signed custom predicate retains
the original credential-free builder's exact workflow/head/run/image/manifests.
The publisher keeps the legacy keyed `cosign sign` for the repository policy and
adds a same-key `--new-bundle-format` signature; SPDX and the custom predicate
are attested in bundle format too. The same digest also carries certificate-based
GitHub build provenance, which makes legacy `verify-attestation --key` fail with
`expected key signature, not certificate` (run 38483704342), so verification uses
`verify-attestation --new-bundle-format --key` and requires the typed
`sign/v1` statement for the digest plus the original SPDX and custom predicates
bound to the fixed destination and digest, as the r13 policy recovery did
(run 38168060273). Policy verification uses a job-local, candidate-repository-scoped
copy of the same public key and `matchRepository` rule. It does not edit
host/stable policy. Its local OCI export uses `--remove-signatures` only because
OCI layout cannot store signatures; Skopeo still checks the source against that
policy and the exported digest is compared.

Candidate registry tags are public by convention, not a confidentiality barrier.
Use only the recorded immutable manifest digest for runtime acquisition. A
trusted exact candidate policy receipt must precede runtime import; preserve
manifest/platform/archive hashes through transfer and compare staged/booted
image and installed/running binaries. OCI import is not proof that registry
signature enforcement occurred. Runtime receipt acquisition remains primary
work and is not established by container smoke/boot checks.

## Canonical authority, packets and receipts

Canonical hashed JSON is UTF-8, sorted keys, compact separators, ASCII escaping
and one real trailing newline (`ci/r13-publication.py::canonical`). Duplicate
JSON fields, unknown top-level fields, noncanonical packet/receipt bytes,
oversized data, missing files, symlinks and changed hashes refuse.

Candidate authority contains `identities` and `reviewReceiptSha256`. Identities
bind tower commit/tree, Snitchwatch source commit/tree/submodule, bridge/daemon
pins hashes and candidate workflow hash. Its independent-workflow-review
receipt binds the SHA256 of that canonical identity object. The control commit
records this after the candidate commit exists, avoiding a self-referential Git
commit/hash. The frozen bridge candidate pin is
`bf15bb5d87b46576b2f2d56d2c5164b6890402c7` (tree
`ac04f33f33145ea39a3ba71fae60fe871aff3f21`, unchanged OpenSnitch gitlink
`b404c4c6316760fa7bc415509d3f8d747f7dc9cc`). This is a candidate identity,
not source review approval. Any review-driven source change requires another
pin/input/verifier refresh and relevant offline checks before publication.

Accepted authority contains `packetSha256` and `receipts` mapping every required
kind to its stored receipt hash. Files live at
`.github/r13/packets/<sha256>.json` and `.github/r13/receipts/<sha256>.json`.
Each receipt has exactly `schemaVersion: 1`, `kind`, `packetSha256`,
`identities`, `reviewer`, `evidenceSha256` and `verdict: PASS`. Required kinds:
independent-head-review, independent-workflow-review, exact-head-ci,
candidate-policy, runtime-nfqueue, runtime-authorization, runtime-matrix,
runtime-reconnect, runtime-migration, runtime-rollback and writer-inventory.

The packet has exactly `schemaVersion: 1`, `identities`, `candidate`,
`publication` and `gui`. Candidate binds build run/artifact ID+archive hash,
candidate metadata hash, immutable manifest/index/platform identity, workflow
and ref. The metadata hash transitively binds every artifact file, installed
bridge/daemon binary and manifest hash. Publication binds the original publisher
run/workflow/commit/ref, which belongs to that same build workflow/run.

GUI binds fixed source repository, source commit/tree/submodule, system profile,
runtime ref, system manifest hash, bundle hash, run/workflow/ref and immutable
artifact ID+hash. It uses source artifact `r13-system-gui-candidate` and file
`snitchwatch-system-r13-candidate.flatpak`. The image's recorded GUI manifest and
runtime must match. Exact head/ref/workflow/repository, green first-attempt CI,
artifact digest/run and retained artifact availability are re-resolved from
GitHub before publication. Source-repository run/artifact metadata is requested
anonymously from the public API, preserving those accepted IDs and hashes.
Tower's repository-scoped job token is used only for tower artifacts.

The promoter below handles the image only. It acquires no GUI bytes; the
packet's GUI binding and the public-release acquisition it described were part
of the retired promoter, so the matching GUI publication is verified and
recorded by the primary in the PR and release notes.

`tests/test-r13-publication.py` provides executable
schemas/fixtures and refusals. CI installs PyYAML for its workflow static checks;
the runtime gate/collector uses only Python's standard library. Regressions
execute the candidate gates, and the promoter's
trust-fetch and SPDX-selection steps against fake `gh`/`cosign`. Offline success
does not establish remote acquisition or runtime success.

## Fixed-digest promotion

`r13-promote.yml` is `workflow_dispatch` only, runs on `main` in this
repository, and takes one required input, `digest`. The workflow hard-codes the
only digest it may promote (`sha256:a6a7a717...bed5f`, the r14 candidate built by `r13-candidate.yml` run 38490106535; r13 itself was `sha256:88f8ff00...b8454`) in `env`, and its first
step fails unless the input equals it. It shares `bazzite-tower-stable-writers`
with the other stable writers, cancellation disabled. One job, permissions
`contents: read` and `packages: write`, every action pinned by SHA, no
`actions/checkout` (the job holds `SIGNING_SECRET`). The public key and host
signature policy are fetched from the API at the dispatched commit.

Steps, in order, any failure stops before the next:

1. Verify `ghcr.io/bearyjd/bazzite-tower-r13-candidate@<digest>` is signed by
   `cosign.pub` and carries a key-signed SPDX attestation. The candidate package
   also holds certificate-based GitHub provenance, so `verify-attestation` uses
   `--new-bundle-format --key` to avoid `expected key signature, not
   certificate`. The verified SPDX predicate for that digest is kept.
2. `skopeo copy --all --preserve-digests` the digest to
   `ghcr.io/bearyjd/bazzite-tower:r13-promotion-<run>`; the pushed digest must
   equal the fixed one.
3. `cosign sign` the digest in the stable repository and re-attest the verified
   SPDX predicate, exactly as `build.yml` does. Re-signing is required: the host
   policy's `matchRepository` rejects the candidate repository's signature.
4. Verify signature and SPDX at the destination, then run `skopeo copy` with the
   host's `policy.json` and `registries.d` (only `keyPath` rewritten to the
   fetched key) into a local OCI directory. That is the check `bootc switch`
   performs.
5. `skopeo copy --all --preserve-digests` the digest to `:snitchwatch-system`.
   No other tag is touched; `:latest` and `:latest-kernel` are out of scope.
6. Re-resolve `:snitchwatch-system`, require the fixed digest, and repeat the
   host-policy copy against the tag. A step summary records the result.

Long cosign calls are bounded to 180 s and verified payloads are discarded or
written to files, never streamed to the log. Run it only after the PR carries
the runtime evidence and review for that digest. Partial publication (staging
tag and signatures written, tag not moved) is safe to re-dispatch.

`ci/r13-publication.py` keeps only the candidate flow's `approval`, `source`
and `candidate` gates. Its retired `promote` entrypoint, accepted-packet/receipt
checks and the `R13_PROMOTION_HEAD` variable were removed; delete that
repository variable if it still exists.

The job runs in the `release` environment so the owner can attach protection.
Referencing a missing environment creates it **unprotected** on the next run, so
set it up first: Settings -> Environments -> release -> required reviewers, and
deployment branches restricted to `main`. This gates the job's start; it does not
hide the repository-level `SIGNING_SECRET`, which `build.yml`, `build-iso.yml`
and `r13-candidate.yml` also read (moving the secret into the environment would
break those).

The `r13-promotion-<run>` staging tag is left in place. GHCR deletes whole
package versions, and after promotion that version also carries
`:snitchwatch-system`, so deleting it by version id would delete the promoted
image. The extra tag points at the same digest and is harmless; a test asserts
the workflow contains no delete step.

Official references: [Skopeo digest-preserving copy](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md),
[public release asset acquisition](https://docs.github.com/en/rest/releases/assets#get-a-release-asset),
[repository-scoped GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token),
[GitHub provenance verification](https://cli.github.com/manual/gh_attestation_verify),
[cosign attestation predicates](https://docs.sigstore.dev/cosign/system_config/specifications/).
