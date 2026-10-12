# r13 candidate and accepted-digest publication

The system stable tag is held. `build.yml` still tests the system profile on PRs,
but push, schedule and ordinary dispatch publish only legacy `safe-pin` and
`latest-kernel`. A post-merge build cannot move `:snitchwatch-system`. Only
`r13-promote.yml` can move that tag, using accepted bytes without a rebuild.

## Reviewed workflow and control trust

The primary sets repository variables only after retrieving native independent
review evidence and exact-head CI, never from candidate-authored PASS data:

| Variable | Meaning |
|---|---|
| `R13_CANDIDATE_HEAD` | Exact independently reviewed tower candidate commit |
| `R13_CONTROL_HEAD` | Exact independently reviewed commit holding gate code, primary authority, packets and receipts |
| `R13_PROMOTION_HEAD` | Exact reviewed, green main commit containing the promoter; leave absent until real runtime acceptance |
| `R13_ACCEPTED_PACKET_SHA256` | Primary-recorded canonical accepted packet hash; leave absent until real runtime acceptance |

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
Policy verification uses a job-local, candidate-repository-scoped copy of the
same public key and `matchRepository` rule. It does not edit host/stable policy.

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
`1527fd4df9070704ae9b98f8c3011461919812e4` (tree
`cc9fa429825b18a592fe4e721d51a0dec42b2476`, unchanged OpenSnitch gitlink
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

Both validation and the checkout-free promoter acquire GUI bytes from the fixed
public `bearyjd/snitchwatch` prerelease `r13-candidate`, asset
`snitchwatch-system-r13-candidate.flatpak`, using identical inline trusted code.
Each checks public repository/prerelease state, the tag's exact accepted source
commit, uploaded asset name/size/digest and fixed API/download URLs. HTTPS
redirects are bounded and restricted to GitHub and its allowlisted asset storage
hosts; these requests carry no Authorization header or inherited proxy. The
download is limited to 512 MiB and ten minutes, and its size and accepted SHA256
must match before use. Release/tag/asset metadata is checked again after
download, and the promoter rehashes the bundle before any stable write. Missing
public assets, inaccessible metadata, changed refs or bytes remain HOLD; no
cross-repository credential or Actions archive-download fallback is provided.
The accepted Actions artifact archive hash remains a separate metadata binding;
it is never compared to or replaced by the release bundle's SHA256.

`tests/test-r13-publication.py` provides executable
schemas/fixtures and refusals. CI installs PyYAML for its workflow static checks;
the runtime gate/collector and inline downloader use only Python's standard
library. Regressions execute the complete unmodified promotion entrypoint and
both actual inline downloaders with mocked HTTP transport, including refusal
paths. Offline success does not establish remote acquisition or runtime success.

## No-rebuild promotion

After real candidate/runtime acceptance and exact reviewed green merges, the
primary sets the promotion variables and manually dispatches `r13-promote.yml`
on main with only `packet_sha256`. All automated stable workflows and r13 flows
share `bazzite-tower-stable-writers`, with cancellation disabled. The promoter
also refuses pending old automatic build runs, which may predate that lock.
The primary must refresh the complete old-writer inventory before promotion.

The read-only gate resolves the trusted packet/receipts, exact green tower/GUI
runs, immutable artifact IDs/hashes and actual accepted image/GUI bytes. The
checkout-free promoter rechecks downloaded bytes, verifies the original
candidate-repository signature/SPDX/custom predicate and exact GitHub workflow
provenance, then copies only the accepted digest with `--all --preserve-digests`
to `ghcr.io/bearyjd/bazzite-tower:promotion-candidate-r13-<run>`.

The automated signing job issues destination-repository-bound signature/SPDX
and the unchanged original candidate-build predicate for that same digest. It
does not generate new build provenance claiming the promotion run built it.
Original candidate GitHub provenance is retained and verified at its original
repository; destination cosign provenance binds the same original metadata.
Destination-bound signature, exact SPDX/build predicates and the unchanged
stable `matchRepository` policy must PASS before `:snitchwatch-system` moves.
The final public manifest hash must equal the accepted digest. Other stable
tags are not r13 publication targets.

The tower job records `IMAGE_PROMOTED_GUI_PENDING`. The matching source lane
must publish its accepted GUI bytes without invoking the old v* rebuild path.
Primary compares both public identities and policy receipts before declaring
joint stable PASS. Partial publication remains partial/HOLD. Retain both CI
artifacts (currently 30 days), public candidate GUI release assets and candidate
attachments until promotion; expired
artifacts require a new candidate/acceptance, never a rebuild shortcut.

Official references: [Skopeo digest-preserving copy](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md),
[public release asset acquisition](https://docs.github.com/en/rest/releases/assets#get-a-release-asset),
[repository-scoped GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token),
[GitHub provenance verification](https://cli.github.com/manual/gh_attestation_verify),
[cosign attestation predicates](https://docs.sigstore.dev/cosign/system_config/specifications/).

## Recovered r13 acceptance (schema v2)

V1 candidate identity and acceptance behavior remain available. V2 acceptance
keeps product/publication at original b2 and digest88, and adds `ciBinding` for
PR107 workflow head H plus `policyRecovery` for fixed recovery38168060273.
Only the three immutable-CI workflow paths may differ; the validator recomputes
Git ancestry, tree, modes and blob identities. Original failed run38153505376
is checked by the pinned recovery helper, not accepted by generic run_receipt.

All existing runtime receipt kinds remain mandatory. Every v2 receipt binds
the packet hash, original identities, ciBinding hash and policyRecovery hash;
independent-product-equivalence and independent-policy-recovery are additional
required kinds. Old receipts cannot be silently converted or labelled H.
Authority remains HOLD until independently endorsed actual evidence exists.

The separately reviewed promotion workflow validates live exact-H CI, required
job success/app ownership and fixed recovery before acquisition, before staging
and immediately before the public pointer. CI failure/cancellation/skipping,
PR-head movement or missing mappings refuses promotion. No image rebuild or
provenance relabelling is authorized by this schema. Field layouts live in
`v2_mapping` and `receipt_v2` in `ci/r13-publication.py`; offline refusal tests
are in `tests/test-r13-publication.py`. Hosted checks remain mandatory.
