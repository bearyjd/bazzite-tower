#!/usr/bin/env python3
"""Offline fixtures for the tower-health boot-chain baseline (Secure Boot, lockdown, LUKS, TPM2).

Commands are faked on a PATH-only directory and /sys, /dev are faked through
TOWER_HEALTH_FSROOT. Every probe has a negative fixture that must report WARN or UNKNOWN.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "system_files/usr/libexec/bazzite-tower-health"
SB_VAR = "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

MOCK = r'''#!/usr/bin/python3
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
env = os.environ
if name == "id":
    print(env.get("MOCK_ID", "1000"))
elif name == "mokutil":
    mode = env.get("MOKUTIL", "enabled")
    if mode == "fail":
        print("failed to read", file=sys.stderr); sys.exit(1)
    print({"enabled": "SecureBoot enabled", "disabled": "SecureBoot disabled",
           "noshim": "SecureBoot enabled\nSecureBoot validation is disabled in shim"}[mode])
elif name == "findmnt":
    src = env.get("ROOT_SRC", "/dev/mapper/luks-root")
    if src == "none": sys.exit(1)
    print(src)
elif name == "lsblk":
    chain = env.get("LSBLK")
    if chain is None: sys.exit(1)
    print(chain.replace(";", "\n"))
elif name == "cryptsetup":
    if args[0] == "status":
        mode = env.get("CS_STATUS", "LUKS2")
        if mode == "denied":
            print("/dev/mapper/x is inactive.", file=sys.stderr); sys.exit(4)
        print("/dev/mapper/luks-root is active and is in use.\n  type:    " + mode +
              "\n  cipher:  aes-xts-plain64\n  keysize: 512 bits")
    elif args[0] == "luksDump":
        mode = env.get("CS_DUMP", "tpm2")
        if mode == "fail": sys.exit(1)
        if mode == "luks1": print("LUKS header information\nVersion:        1"); sys.exit()
        print("LUKS header information\nVersion:        2\nTokens:")
        if mode == "tpm2": print("  0: systemd-tpm2")
        elif mode == "fido": print("  0: systemd-fido2")
        print("Digests:\n  0: pbkdf2")
else:
    raise SystemExit("unexpected command: " + name)
'''

LUKS_CHAIN = "crypt luks-root /dev/mapper/luks-root;part nvme0n1p3 /dev/nvme0n1p3;disk nvme0n1 /dev/nvme0n1"
PLAIN_CHAIN = "part nvme0n1p3 /dev/nvme0n1p3;disk nvme0n1 /dev/nvme0n1"


class BootChain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tower-bc-test-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.fs = self.base / "fs"
        (self.fs / "sys/firmware/efi/efivars").mkdir(parents=True)
        (self.fs / "sys/kernel/security").mkdir(parents=True)
        (self.fs / "sys/class/tpm/tpm0").mkdir(parents=True)
        (self.fs / "dev").mkdir()
        (self.fs / "sys/kernel/security/lockdown").write_text("none [integrity] confidentiality\n")
        (self.fs / "sys/class/tpm/tpm0/tpm_version_major").write_text("2\n")
        (self.fs / "dev/tpmrm0").write_text("")
        for name in ("timeout", "head", "tr", "awk", "python3", "grep", "tail", "cut", "od"):
            (self.bin / name).symlink_to(shutil.which(name))
        for name in ("id", "mokutil", "findmnt", "lsblk", "cryptsetup"):
            p = self.bin / name
            p.write_text(MOCK)
            p.chmod(0o755)
        self.env = {"PATH": str(self.bin), "TOWER_HEALTH_FSROOT": str(self.fs),
                    "LSBLK": LUKS_CHAIN, "MOCK_ID": "0"}

    def run_bc(self, **env):
        result = subprocess.run(["/bin/bash", str(HEALTH)], env={**self.env, **env},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("== Boot-chain baseline", result.stdout)
        out = result.stdout.split("== Boot-chain baseline", 1)[1]
        return {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
                for line in out.splitlines()[1:] if line.startswith("  ") and ":" in line[:34]}

    # Secure Boot
    def test_sb_enabled(self):
        self.assertTrue(self.run_bc()["Secure Boot"].startswith("PASS"))

    def test_sb_disabled_warns(self):
        self.assertTrue(self.run_bc(MOKUTIL="disabled")["Secure Boot"].startswith("WARN"))

    def test_sb_shim_validation_disabled_warns(self):
        self.assertTrue(self.run_bc(MOKUTIL="noshim")["Secure Boot"].startswith("WARN"))

    def test_sb_non_efi_warns(self):
        shutil.rmtree(self.fs / "sys/firmware")
        value = self.run_bc()["Secure Boot"]
        self.assertIn("not an EFI boot", value)
        self.assertTrue(value.startswith("WARN"))

    def test_sb_efivar_fallback(self):
        var = self.fs / "sys/firmware/efi/efivars" / SB_VAR
        var.write_bytes(bytes([7, 0, 0, 0, 1]))
        self.assertTrue(self.run_bc(MOKUTIL="fail")["Secure Boot"].startswith("PASS"))
        var.write_bytes(bytes([7, 0, 0, 0, 0]))
        self.assertTrue(self.run_bc(MOKUTIL="fail")["Secure Boot"].startswith("WARN"))

    def test_sb_unreadable_is_unknown_never_pass(self):
        self.assertTrue(self.run_bc(MOKUTIL="fail")["Secure Boot"].startswith("UNKNOWN"))

    # Lockdown
    def test_lockdown_modes(self):
        f = self.fs / "sys/kernel/security/lockdown"
        for text, prefix in (("none [integrity] confidentiality", "PASS"),
                             ("none integrity [confidentiality]", "PASS"),
                             ("[none] integrity confidentiality", "WARN"),
                             ("garbage", "UNKNOWN")):
            f.write_text(text + "\n")
            self.assertTrue(self.run_bc()["Kernel lockdown"].startswith(prefix), text)

    def test_lockdown_missing_is_unknown(self):
        (self.fs / "sys/kernel/security/lockdown").unlink()
        self.assertTrue(self.run_bc()["Kernel lockdown"].startswith("UNKNOWN"))

    # Root encryption
    def test_root_luks2(self):
        value = self.run_bc()["Root encryption"]
        self.assertTrue(value.startswith("PASS LUKS2"), value)
        self.assertIn("aes-xts-plain64", value)

    def test_root_luks1_warns(self):
        self.assertTrue(self.run_bc(CS_STATUS="LUKS1")["Root encryption"].startswith("WARN LUKS1"))

    def test_root_plain_dmcrypt_warns(self):
        self.assertTrue(self.run_bc(CS_STATUS="PLAIN")["Root encryption"].startswith("WARN plain"))

    def test_root_unencrypted_warns(self):
        value = self.run_bc(ROOT_SRC="/dev/nvme0n1p3", LSBLK=PLAIN_CHAIN)["Root encryption"]
        self.assertTrue(value.startswith("WARN"), value)

    def test_root_crypt_status_unreadable_is_unknown(self):
        self.assertTrue(self.run_bc(CS_STATUS="denied")["Root encryption"].startswith("UNKNOWN"))

    def test_root_source_not_block_device_is_unknown(self):
        self.assertTrue(self.run_bc(ROOT_SRC="composefs")["Root encryption"].startswith("UNKNOWN"))

    def test_lsblk_failure_is_unknown(self):
        self.env = {k: v for k, v in self.env.items() if k != "LSBLK"}
        self.assertTrue(self.run_bc()["Root encryption"].startswith("UNKNOWN"))

    # TPM presence
    def test_tpm_present(self):
        self.assertTrue(self.run_bc()["TPM"].startswith("PASS"))

    def test_tpm_absent_warns(self):
        shutil.rmtree(self.fs / "sys/class/tpm")
        result = self.run_bc()
        self.assertTrue(result["TPM"].startswith("WARN"))
        self.assertTrue(result["TPM2 LUKS keyslot"].startswith("WARN"))

    def test_tpm_without_resource_manager_warns(self):
        (self.fs / "dev/tpmrm0").unlink()
        self.assertTrue(self.run_bc()["TPM"].startswith("WARN"))

    def test_tpm12_warns(self):
        (self.fs / "sys/class/tpm/tpm0/tpm_version_major").write_text("1\n")
        self.assertTrue(self.run_bc()["TPM"].startswith("WARN"))

    # TPM2 keyslot: root vs non-root
    def test_keyslot_enrolled_as_root(self):
        self.assertTrue(self.run_bc()["TPM2 LUKS keyslot"].startswith("PASS"))

    def test_keyslot_non_root_unknown(self):
        self.assertTrue(self.run_bc(MOCK_ID="1000")["TPM2 LUKS keyslot"].startswith("UNKNOWN"))

    def test_keyslot_not_enrolled_warns(self):
        self.assertTrue(self.run_bc(CS_DUMP="none")["TPM2 LUKS keyslot"].startswith("WARN"))
        self.assertTrue(self.run_bc(CS_DUMP="fido")["TPM2 LUKS keyslot"].startswith("WARN"))

    def test_keyslot_luks1_warns(self):
        self.assertTrue(self.run_bc(CS_DUMP="luks1")["TPM2 LUKS keyslot"].startswith("WARN"))

    def test_keyslot_dump_failure_unknown(self):
        self.assertTrue(self.run_bc(CS_DUMP="fail")["TPM2 LUKS keyslot"].startswith("UNKNOWN"))

    def test_keyslot_unencrypted_root_unknown(self):
        value = self.run_bc(ROOT_SRC="/dev/nvme0n1p3", LSBLK=PLAIN_CHAIN)["TPM2 LUKS keyslot"]
        self.assertTrue(value.startswith("UNKNOWN"), value)


if __name__ == "__main__":
    unittest.main()
