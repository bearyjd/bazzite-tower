# Left-click loss (Arc Touch BT mouse) — investigation notes

*Started: 2026-10-03 · Status: **OPEN, experiment in progress** · Nothing in the image was changed; the one change made is machine-local (see "Experiment").*

> Hardware: ThinkPad P1 Gen 7, Bazzite bootc (`latest.20260927`), kernel `7.2.0-ogc6.1`, KDE Plasma 6.7.4 (Wayland), libinput 1.31.3. Pointing devices: Sensel haptic touchpad `SNSL002D` (`event7`), TrackPoint (`event4`), **Microsoft Arc Touch BT Mouse** (`045E:0804`, Bluetooth, `event15`).

## Symptom

Left click (maybe right) sometimes does nothing and needs several tries. Movement and scroll are fine. **Worst on the Arc mouse**, much milder on touchpad/TrackPoint. It feels like a software state, "like a click-and-drag is held". **Tapping left Shift on the laptop keyboard makes the next click work.** It fails intermittently, then works, then fails again. Reported as recent; exact start unknown.

## Evidence (all observed on this machine, 2026-10-03)

| Observation | Source |
|---|---|
| Failed clicks are **absent from kwin's own event list** (KWin debug console → Input Events); working clicks appear | user, live |
| A separate `libinput debug-events` context **does see the same failing clicks** | capture below |
| In the failing state every Arc click is a **6–8 ms press/release pulse** (8, 23, 8, 8, 6, 7 ms); in good runs holds are 60–200 ms | capture |
| Occasional press→release→press bounce with an 8 ms gap on the Arc mouse (e.g. +12.614 rel / +12.622 press) | capture |
| Touchpad + TrackPoint: 10 of 10 test clicks reached libinput, clean ~0.86 s cadence, 215–285 ms holds | capture |
| `libinput quirks list` for the Arc mouse was **empty**; libinput ships `ModelBouncingKeys=1` for the sibling Microsoft Nano Transceiver (`045E:0800`) but nothing for `0804` | `/usr/share/libinput/30-vendor-microsoft.quirks` |
| While failing, kwin_wayland: 0.2 % CPU, all threads idle; plasmashell idle in `poll`; GPU P3 / 10 % | live snapshot |

Capture used (run in a normal terminal; `sudo` is needed, and `--show-keycodes` logs key names, so do not type secrets while it runs):

```
timeout 40 sudo libinput debug-events --show-keycodes 2>&1 | grep -E 'KEYBOARD_KEY|POINTER_BUTTON'
```

## Ruled out (with the evidence)

- **Hardware switch / Bluetooth link loss as the whole story** — a fresh libinput context receives the failing clicks; they are lost inside kwin's context.
- **Compositor stall** — kwin idle at 0.2 % CPU while failing; no kwin restarts/crashes; `stall-detect` logged nothing this boot.
- **Input remapper / grabber** — `input-remapper-service` runs but errors on every autoload (`set_config_dir`), has no preset, creates no virtual devices. No uinput/virtual input devices present. No other grabbers found.
- **Bluetooth controller power management** — USB `3-10` (`8087:0036`) is `control=on`, `active`; `btusb` autosuspend off; Wi-Fi power-save off.
- **Accessibility / sticky keys / XKB option** — no `kaccessrc`; `kxkbrc` is plain `us`; no modifier-only shortcuts.
- **Terminal mouse-reporting leftovers** — it fails on the desktop too, not only in terminals.
- **Recent package upgrade** — `rpm-ostree db diff` `latest.20260920` → `latest.20260927` changed only `containerd.io` and `selinux-policy` (44.9→44.10). Input stack (kernel, kwin, libinput, bluez, Xwayland) identical. No SELinux denials touching input/bluetooth/kwin.
- **Interference** — considered (Wi-Fi on 2.4 GHz ch 9 / 40 MHz next to a classic-BT mouse); disfavoured because movement/scroll are unaffected and the failure is inside kwin. Not directly tested.

## Working hypothesis (UNCONFIRMED)

The Arc mouse's button chatters (short pulses, bounces). libinput's debounce state in kwin's long-running context then delays or swallows some clicks; a new key event (Shift tap) makes libinput process the pending state. The kwin-side drop is established; **the debounce step is not proven**.

Also noted: kwin has run since 2026-10-01 13:50 (first boot on `latest.20260927`), and the Bluetooth resume guard (`bazzite-tower-bluetooth-resume-guard`, added 2026-09-19) restarted `bluetooth.service` 4 times this boot, recreating the mouse's input device each time. A long-session-state cause is therefore plausible and is **not separated** from the quirk by the experiment below.

## Experiment (machine-local, NOT in the repo)

Appended to `/etc/libinput/local-overrides.quirks` (file is not owned by any package and not tracked here; a dated backup was made next to it). It sits below the existing touchpad `AttrPalmSizeThreshold=6` stanza, which was left untouched:

```
[Microsoft Arc Touch BT Mouse]
MatchUdevType=mouse
MatchBus=bluetooth
MatchVendor=0x045E
MatchProduct=0x0804
ModelBouncingKeys=1
```

Verified applied: `libinput quirks list /dev/input/event15` → `ModelBouncingKeys=1`. It only takes effect in kwin after a **new login session** (log out/in). Check `ps -o etime= -p $(pgrep -x kwin_wayland)` is short afterwards.

**Revert:** restore the dated `local-overrides.quirks.bak-*` copy over `/etc/libinput/local-overrides.quirks`, then log out/in.

## Open questions

- When did it first fail — after the 2026-10-01 13:50 reboot, after a specific wake from sleep (resumes at 2026-10-02 ~20:25 and ~21:51), or earlier?
- Does it start right after a resume, or come on mid-session?
- Does the mouse behave the same paired to another device (phone/other PC)? Untested; would isolate the mouse itself.

## Next steps by outcome

- **Good for hours/days after the login:** quirk or fresh session fixed it (not separable). Decide whether to bake the quirk into the image. If so, treat it as a hardware-tuning change per `CLAUDE.md` (evidence above) and update `docs/CODEMAPS/system-files.md` and `tests/smoke.sh` in the same change.
- **Fails again:** drop the debounce hypothesis. Leave a long-running raw capture alongside the KWin debug console (`sudo libinput debug-events --show-keycodes > ~/libinput-long.log`) and compare the same click in both. Also test the mouse on another device, and consider the disabled-but-enabled `breezy_desktop` kwin effect (QML fails to load at every login; no evidence it matters).

## Not changed

- Touchpad palm quirk (`AttrPalmSizeThreshold=6`) and all `kargs.d` / RAS / audio tuning.
- `~/.config/kcminputrc` (touchpad is `ClickMethod=2` clickfinger, `DisableEventsOnExternalMouse=true`; not A/B-tested yet — the touchpad was not the main failure).
