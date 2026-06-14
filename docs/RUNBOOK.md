# Runbook

Operational procedures for the `bazzite-tower` image. There is no server to deploy —
the "deployment" is a bootc image the target machine rebases onto. Day-2 operations
are install, update, **rollback**, health-check, triage, and the CI publish pipeline.

Image: `ghcr.io/bearyjd/bazzite-tower:latest` · signed with `cosign.pub`.

## Install / switch

```bash
sudo bootc switch ghcr.io/bearyjd/bazzite-tower:latest
sudo systemctl reboot
```

From any bootc host (Bazzite, Bluefin, Aurora, Silverblue, Fedora Atomic). Bazzite's
policy enforces signature verification against `cosign.pub`. See
[README "Installing"](../README.md#installing).

## Update

Bazzite applies image updates in the background. Force one immediately with:

```bash
sudo bootc upgrade
sudo systemctl reboot
```

`:latest` rebuilds weekly (Sun 06:00 UTC) and on every push to `main`.

## Rollback (do this first when a boot goes bad)

```bash
sudo bootc rollback        # stage the previous deployment
sudo systemctl reboot
```

Or pick the previous entry in the GRUB boot menu at power-on. To **freeze** on a
known-good build instead of tracking `:latest`, switch to a date-stamped tag:

```bash
sudo bootc switch ghcr.io/bearyjd/bazzite-tower:latest.YYYYMMDD
```

Tag scheme (`latest`, `latest.YYYYMMDD`, `YYYYMMDD`, `<short-sha>`):
[README "Tags"](../README.md#tags).

## Health checks

| Check | Command |
|---|---|
| Current/rollback deployment, signature | `bootc status` |
| Suspend mode actually in effect | `cat /sys/power/mem_sleep` (bracketed entry is active; expect `[s2idle]`) |
| Audio: SOF bypass active | `journalctl -k -b 0 \| grep -i dsp_driver` and `grep -c "FW reported error: 9"` should be 0; `cat /proc/asound/cards` shows the HDA card |
| CPU MCE / RAS summary | `sudo ras-mc-ctl --summary` · `sudo ras-mc-ctl --errors` |
| Running CPU microcode revision | `grep -m1 microcode /proc/cpuinfo` (and `journalctl -k \| grep -i microcode`) |
| Tracked kernel args applied (no dupes) | `cat /proc/cmdline` — expect each tracked karg exactly once (IOMMU, `kvmfr.static_size_mb=128`, `vfio_pci.disable_vga=1`, `kvm.ignore_msrs=1`, `nvme_core.default_ps_max_latency_us=0`) |
| NVMe SMART health / self-tests | `sudo smartctl -H /dev/nvme0` · `/dev/nvme1`; smartd warnings + scheduled tests: `journalctl -u smartd` |
| Swappiness in effect | `sysctl vm.swappiness` (expect `10`) |
| Indexer excludes applied | `balooctl6 config show excludeFilters` (expect `.gradle`, build/cache dirs) |
| CPU power baseline | `cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference` (expect `balance_performance`); `cat /sys/firmware/acpi/platform_profile` (expect `balanced`); `systemctl is-active thermald` |
| Virt stack up | `systemctl is-active virtqemud.socket` · `virsh -c qemu:///system list --all` |
| Default NAT network | `ujust vm-net-status` |
| Wi-Fi diagnostics (offline) | `ujust wifi-debug` |

**One-shot sweep:** [`scripts/tower-diagnostic.sh`](../scripts/tower-diagnostic.sh)
runs all of the above (SOF/ABI, MCE/RAS, i915 resume, thermals, SMART, rpm-ostree)
in one pass. Run with `sudo` for the root-only checks:
`sudo ./scripts/tower-diagnostic.sh`.

CI mirrors these: `tests/smoke.sh` (offline, the gate) and `tests/boot-check.sh`
(runtime). See [docs/CODEMAPS/ci-cd.md](./CODEMAPS/ci-cd.md).

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Wi-Fi gone after a rebase | stale `wifi.backend=iwd` with iwd not enabled | the `wifi-backend-guard` service auto-recovers on boot; inspect with `ujust wifi-debug` |
| `virtqemud` won't start | upstream change dropped the `qemu` system user | rebuilt/guarded in `build.sh`; the smoke + boot tests catch regressions |
| Can't manage VMs as your user | user not yet in `kvm`/`libvirt`/`docker` | `ujust fix-vm-groups`, then re-login (the first-boot oneshot adds the first user automatically) |
| `docker.socket` fails at boot (`Failed to resolve group 'docker'`) | the `docker` group wasn't baked into the image (stale gshadow orphan made `systemd-sysusers` abort, so the group got created late) | `build.sh` now strips all shadow/gshadow orphans and bakes `groupadd -r docker`; the smoke test asserts the group exists |
| Display flicker / ~30s sluggish wake | i915 PSR/DC or `deep` suspend on Meteor Lake | baked kargs disable PSR/DC and pin `s2idle`; verify `cat /sys/power/mem_sleep` |
| No audio; journal floods with `FW reported error: 9` / `failed to create module pipeline` | SOF topology ABI (3.29) newer than the kernel's SOF driver ABI (3.23); no ABI-≤3.23 firmware in repos to downgrade to | `25-audio-sof-bypass.toml` forces the legacy HDA driver (`snd_intel_dspcfg.dsp_driver=1`), sidestepping SOF; verify `journalctl -k \| grep -i dsp_driver` |
| Frequent corrected MCEs in the journal | corrected CPU **cache** errors on Meteor Lake (EDAC `igen6` ECC counters 0/0 → not DRAM) | `rasdaemon` records/decodes them; `mcelog` is masked (its trigger tried to offline a CPU). Decode with `sudo ras-mc-ctl --errors` |
| `smartd` warns of media errors / available-spare drop | NVMe wear or developing fault | `journalctl -u smartd`; confirm with `sudo smartctl -a /dev/nvmeN`; a falling available-spare or rising media-error count is an escalation/back-up signal |
| Secure Boot refuses the image | — | the image kernel is signed with the shared ublue MOK (already enrolled on ublue/Bazzite hosts); no MOK work needed when switching ublue↔bazzite-tower |

## Audio: SOF bypass (legacy HDA)

**Hardware** (verified on the live box): Realtek **ALC287** HDA codec (headphones +
analog/headset mic), **TI TAS2781** smart-amp speakers bound to the ALC287 as an HDA
*side-codec* (`tas2781_hda_comp_ops`), Intel HDMI codec, and a 2-mic **DMIC** array
that is SOF/DSP-only.

The kernel's SOF IPC4 driver is at topology ABI **3.23**, but stock
`alsa-sof-firmware` (2025.12.2) ships topologies at ABI **3.29**. When playback
starts on `pcm0p` ("HDA Analog" = speakers/headphones) the kernel sends a
module-create IPC the firmware (ADSPFW 2.14.1.1) can't parse — `failed to create
module pipeline.1` / `ipc error 0x11000007` / `ASoC error (-22) at
snd_soc_pcm_component_prepare` — and PipeWire retries at ~10 Hz (**94k+** errors per
boot, dead audio). Fedora's repos no longer carry an ABI-≤3.23 `alsa-sof-firmware`,
so the firmware **cannot be downgraded** to match the kernel.

- **Fix in the image:** `kargs.d/25-audio-sof-bypass.toml` sets
  `snd_intel_dspcfg.dsp_driver=1`, forcing the **legacy `snd_hda_intel` driver**.
  Because the speakers (TAS2781 via ALC287), headphones, HDMI, and analog/headset
  mic all live on the HDA codec path, they all work on legacy HDA — this is the
  documented SOF workaround (upstream even hardcodes legacy HDA for some ThinkPads
  in `intel-dsp-config`). **Lost:** the 2 internal DMICs and SOF DSP effects. Revert
  by deleting the fragment (re-enables SOF).
- **Verify after reboot:** `journalctl -k | grep -i dsp_driver`,
  `journalctl -k | grep -c "FW reported error: 9"` (expect `0`), and
  `cat /proc/asound/cards` (one HDA card, no SOF storm).
- **Seatbelt (dormant while bypassed):** `…/wireplumber.conf.d/90-tower-sof-backoff.conf`
  shortens the audio node's idle/error suspend window. It only matters if SOF is
  re-enabled (fragment removed); harmless otherwise.
- **Alternative considered, not taken:** overlay a matched ABI-3.23 firmware +
  topology set from [thesofproject/sof-bin](https://github.com/thesofproject/sof-bin)
  or Koji to keep full SOF + the mic array — more complex, depends on an external
  source, and needs per-update version matching. Revisit if the DMIC array is needed.

## CPU MCEs (corrected cache errors)

This Meteor Lake CPU logs corrected machine-check events (~115/boot observed) that
are **CPU cache**, not DRAM — EDAC `igen6` ECC counters stay at 0/0. `rasdaemon`
collects and decodes them; `mcelog` is masked.

```bash
sudo ras-mc-ctl --summary    # counts by type since boot
sudo ras-mc-ctl --errors     # decoded per-event detail (bank, address, type)
```

Reading the result:

- **Corrected** errors spread across cores/cache ways are common and generally
  benign — the CPU corrected them and continued.
- Corrected errors **localized to a single core / cache line** that recur are a
  possible RMA signal; capture `ras-mc-ctl --errors` over several boots.
- **Any _uncorrected_ MCE is an escalation** — treat as failing hardware: save the
  decode, and roll back / power down rather than continue.

Microcode: `microcode_ctl` is layered at the latest Fedora revision in `build.sh`.
Early-load takes effect once the initramfs is regenerated (on a base bump); confirm
the running revision with `grep -m1 microcode /proc/cpuinfo`.

## Module blacklists (lean boot)

`…/modprobe.d/blacklist-unused-gpu.conf` blacklists **amdgpu** / **amdxcp** — there
is no AMD GPU on this machine, and no display path (including DisplayPort-alt over
USB-C/Thunderbolt, which the Intel iGPU drives) depends on them. `xe` is left
loaded on purpose. Revert by deleting the file. If `lsmod | grep amdgpu` still
shows it loaded after a rebase, it's initramfs-embedded — add the kernel arg
`rd.driver.blacklist=amdgpu` (new kargs.d fragment) as the stronger lever.

## Tuning defaults (swappiness, indexer)

- `…/sysctl.d/99-tower-swappiness.conf` sets `vm.swappiness=10` (zram was filling
  while RAM was free). Override at `/etc/sysctl.d/`; verify `sysctl vm.swappiness`.
- `/etc/xdg/baloofilerc` seeds baloo's `exclude filters` with build/cache trees
  (`.gradle`, `target`, `build`, language caches; `node_modules` is already a baloo
  default). It only seeds new users — a user's `~/.config/baloofilerc` overrides it.
  Re-index after editing with `balooctl6 disable && balooctl6 enable`.

## CPU power & thermal

The box boots into the firmware's most conservative state — cpufreq
`EPP=power`, ACPI `platform_profile=low-power` — with no power daemon, throttling a
plugged-in homelab. The image sets a **balanced** baseline:

- `bazzite-tower-power-tuning.service` (oneshot, runs `…/libexec/bazzite-tower-power-tuning`
  at boot) sets `platform_profile=balanced` and cpufreq `EPP=balance_performance`
  on every core. Idempotent; skips any knob that's absent/read-only.
- `thermald.service` manages Meteor Lake thermal limits.
- **Want more/less?** For max throughput edit the helper to write `performance`
  (EPP) / `performance` (profile); to revert, mask the service
  (`sudo systemctl mask bazzite-tower-power-tuning.service`). EPP can reset across
  suspend — re-run `sudo /usr/libexec/bazzite-tower-power-tuning` after resume, or
  add a `systemd-sleep` hook if that matters.

Runtime surface (units, helpers, kargs): [docs/CODEMAPS/system-files.md](./CODEMAPS/system-files.md).

## Publish pipeline (operator view)

`build.yml`: build → **smoke gate** → push GHCR → cosign sign **by digest**. The gate
runs before login/push, so a broken image never reaches `:latest` (it stays last-good).
Each guard workflow opens — and later auto-closes — a labelled tracking issue:

<!-- AUTO-GENERATED:ci-labels (from .github/workflows/) -->
| Label | Workflow | Meaning |
|---|---|---|
| `ci-failure` | `build.yml` | build or smoke gate failed; nothing published |
| `boot-test-failure` | `boot-test.yml` | image built but misbehaved at runtime |
| `base-bump` | `base-watch.yml` | upstream base changed a blast-radius package |
| `iso-failure` | `build-iso.yml` | titanoboa live/installer ISO build failed |
<!-- END AUTO-GENERATED:ci-labels -->

## CI secrets

<!-- AUTO-GENERATED:secrets (from .github/workflows/) -->
| Secret | Required | Used for |
|---|---|---|
| `GITHUB_TOKEN` | auto | GHCR push; open/close tracking issues (provided by Actions) |
| `SIGNING_SECRET` | optional | cosign private key — sign image by digest (`build.yml`) and ISO sign-blob (`build-iso.yml`). Unset → signing step skipped |
| `S3_PROVIDER`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_ENDPOINT`, `S3_BUCKET_NAME` | optional | rclone upload of disk/ISO artifacts (`build-disk.yml`, `build-iso.yml`). Unset → artifact-only |
<!-- END AUTO-GENERATED:secrets -->

Rotate `SIGNING_SECRET` by generating a new cosign keypair, updating the repo secret,
and committing the new `cosign.pub`.

## Verify a published image

```bash
cosign verify --key cosign.pub ghcr.io/bearyjd/bazzite-tower:latest
```

## Disk / ISO artifacts

- `build-disk.yml` (dispatch) → qcow2 via bootc-image-builder → artifact or S3.
- `build-iso.yml` (dispatch, Sun 08:00 UTC) → titanoboa live/installer ISO,
  Secure-Boot-bootable → checksum + cosign sign-blob → artifact or S3. Build internals:
  [docs/CODEMAPS/iso-build.md](./CODEMAPS/iso-build.md).
