#!/usr/bin/env python3
"""Create a deterministic SPDX 2.3 predicate from an installed-RPM TSV."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_created(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid --created timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("--created timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_inventory(path: Path) -> list[tuple[str, str, str, str, str]]:
    records: list[tuple[str, str, str, str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 5 or any(not field or field != field.strip() for field in fields):
            raise ValueError(f"invalid RPM inventory row {line_number}")
        name, epoch, version, release, arch = fields
        if not epoch.isdecimal():
            raise ValueError(f"invalid RPM epoch at row {line_number}")
        records.append((name, epoch, version, release, arch))

    if not records:
        raise ValueError("RPM inventory is empty")
    if len(set(records)) != len(records):
        raise ValueError("RPM inventory contains duplicate EVRA records")
    return sorted(records)


def package_id(record: tuple[str, str, str, str, str]) -> str:
    digest = hashlib.sha256("\t".join(record).encode("utf-8")).hexdigest()[:16]
    return f"SPDXRef-RPM-{digest}"


def make_document(records: list[tuple[str, str, str, str, str]], created: str) -> dict:
    canonical = "\n".join("\t".join(record) for record in records)
    canonical += f"\ncreated={created}"
    inventory_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    packages = []
    relationships = []

    for name, epoch, version, release, arch in records:
        record = (name, epoch, version, release, arch)
        spdx_id = package_id(record)
        packages.append(
            {
                "SPDXID": spdx_id,
                "name": name,
                "versionInfo": f"{epoch}:{version}-{release}.{arch}",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": spdx_id,
            }
        )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "bazzite-tower installed RPM inventory",
        "documentNamespace": (
            "https://spdx.org/spdxdocs/bazzite-tower-rpm-inventory-"
            f"{inventory_digest}"
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: bazzite-tower-rpm-inventory-spdx-1.0.0"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="strict five-field RPM TSV")
    parser.add_argument("--output", type=Path, required=True, help="SPDX JSON output path")
    parser.add_argument("--created", required=True, help="RFC 3339 SPDX creation timestamp")
    args = parser.parse_args()

    try:
        created = parse_created(args.created)
        document = make_document(parse_inventory(args.input), created)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
