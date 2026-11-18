#!/usr/bin/env python3
"""Offline test for the smoke MOTD image-tag assertion.

Extracts the jq program from tests/smoke.sh and runs it against fixture
image-info.json documents: a system bridge profile must carry a
snitchwatch-system* tag, a legacy profile must carry a latest* tag.
"""
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = (ROOT / "tests/smoke.sh").read_text()
FILTER = re.search(r'jq --arg bridge_profile "[^\n]*" \\\n\s+-e \'(.*?)\' \\\n', SMOKE, re.S).group(1)


def passes(profile, tag):
    doc = {
        "image-name": "bazzite-tower",
        "image-ref": "ostree-image-signed:docker://ghcr.io/bearyjd/bazzite-tower",
        "image-tag": tag,
        "image-branch": tag,
    }
    return subprocess.run(
        ["jq", "--arg", "bridge_profile", profile, "-e", FILTER],
        input=json.dumps(doc), text=True, capture_output=True,
    ).returncode == 0


class MotdTag(unittest.TestCase):
    def test_system_profile_rejects_latest(self):
        self.assertFalse(passes("system", "latest"))
        self.assertFalse(passes("system", "latest-kernel"))

    def test_system_profile_accepts_system_tags(self):
        self.assertTrue(passes("system", "snitchwatch-system"))
        self.assertTrue(passes("system", "snitchwatch-system-candidate"))

    def test_legacy_profile_accepts_latest_tags(self):
        self.assertTrue(passes("legacy", "latest"))
        self.assertTrue(passes("legacy", "latest-kernel"))

    def test_legacy_profile_rejects_system_tags(self):
        self.assertFalse(passes("legacy", "snitchwatch-system"))


if __name__ == "__main__":
    unittest.main()
