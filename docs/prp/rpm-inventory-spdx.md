# RPM-inventory SPDX release gate

## Goal

Replace the release workflow's full filesystem Syft scan with a deterministic
SPDX 2.3 inventory of the RPMs installed in the exact locally tagged candidate
image. The release gate must complete reliably before a candidate is pushed,
while preserving the existing digest-bound Cosign SBOM attestation, signature
verification, provenance attestation, and verified-digest promotion.

## Approach

1. Add a standard-library Python generator that accepts a strict TSV RPM
   inventory (`name`, `epoch`, `version`, `release`, `arch`) and emits stable
   SPDX JSON. It rejects malformed, duplicate, or empty inventory input.
2. Add fixture-driven tests for determinism, exact EVRA retention, SPDX
   structure/relationships, and invalid input.
3. In the release-only workflow, query RPM from the just-tagged local
   candidate, generate and validate the SPDX predicate, then reuse the current
   Cosign attestation and promotion sequence unchanged.
4. Update source-level workflow assertions and CI documentation to encode that
   this is an installed-RPM inventory, not a whole-filesystem or language
   dependency catalog.

## Files and areas

- `ci/rpm-inventory-spdx.py` and `tests/test-rpm-inventory-spdx.sh`
- `tests/fixtures/rpm-inventory-spdx/`
- `.github/workflows/build.yml`
- `tests/check-supply-chain.sh`
- `docs/CODEMAPS/ci-cd.md`

## Out of scope

- Changing the candidate push, signing, Cosign attestation, provenance, or
  promotion model.
- Replacing RPM inventory with an all-files/language-package SBOM.
- Adding third-party scanner downloads, scanners, secrets, or external
  network calls to the SBOM generation gate.

## Risks and acceptance criteria

This deliberately narrows the predicate coverage to installed RPM packages;
non-RPM files and language dependency metadata are not cataloged. The
inventory must be taken from the exact local candidate before its first push,
be nonempty and structurally valid SPDX, be deterministic under input reorder,
and remain attached and verified against the eventual immutable candidate
digest before public tags are promoted.
