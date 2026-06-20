# Bisect runbook — i915 MTL cx0/C10 PLL s2idle-resume regression

Goal: produce a **first-bad-commit** (`git bisect`) between `v6.19` (good) and `v7.0` (bad) to hand a maintainer, converting our "suspect series" into a proven culprit. Pairs with `REPORT.md`.

> Verify the real tag names in your tree first (`git tag | grep -E '^v(6\.19|7\.0)$'`); the labels below assume the research's v6.19-good / v7.0-bad boundary.

## Hard constraints (read first)

1. **Real hardware only.** The bug needs the physical MTL C10 PHY / eDP panel — it does **not** reproduce in a VM (virtio-GPU). Bisect on the P1 Gen 7 itself.
2. **NVIDIA is not needed.** The fault is on the Intel iGPU driving eDP-1. Bisect with **vanilla kernels and the dGPU driver blacklisted** (`modprobe.blacklist=nouveau,nvidia`) — this sidesteps the akmod-version-matching problem entirely. You only need `i915`.
3. **Secure Boot.** Self-built kernels are unsigned. Either **disable Secure Boot for the bisect session** (simplest for ~12 builds) or self-sign each with your MOK (tedious). Re-enable SB after.
4. **Don't bisect on the atomic deployment.** `rpm-ostree`/ostree make swapping ~12 arbitrary-commit kernels painful. Do this on a **conventional Linux install** (a spare SSD / USB / partition) where `make && make install && grub2-mkconfig` is normal. The bazzite install stays untouched; you just need to boot the same metal.

## Fastest path first — confirm the suspect series (2 boots, not 12)

Before a full bisect, test the boundary of the DPLL-framework series directly:

```bash
git clone --depth=1 ... # or a full torvalds/linux clone
# A = parent of the series lead commit (should be GOOD)
git checkout 1a7fad2aea74~1
# build, boot, test (protocol below)  -> expect GOOD
# B = series tip / a bit after (should be BAD)
git checkout 1a7fad2aea74            # or the last commit of the 32-patch series
# build, boot, test  -> expect BAD
```
If A=good and B=bad, you've nailed it to the series with two boots and can either stop (report that) or bisect *within* the series for the exact commit. If A is already bad, the regression is elsewhere and you need the full bisect.

## Full bisect

### Environment (on the conventional install, on the metal)

```bash
sudo dnf install -y gcc make flex bison elfutils-libelf-devel openssl-devel \
  ncurses-devel bc dwarves perl   # Fedora deps; adjust per distro
git clone https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
cd linux
```

### Kernel config (do once, reuse via bisect)

Seed from a known-good config so i915/DRM/MTL are enabled:
```bash
zcat /proc/config.gz > .config 2>/dev/null || cp /boot/config-$(uname -r) .config
scripts/config -e DRM -e DRM_I915 -e DRM_I915_FORCE_PROBE   # ensure i915 present
# keep it light: localmodconfig trims to your hardware (faster builds)
yes '' | make localmodconfig
make olddefconfig
```

### The loop

```bash
git bisect start v7.0 v6.19      # bad good
# for each step git checks out a commit:
make olddefconfig
make -j"$(nproc)" && sudo make modules_install install
sudo grub2-mkconfig -o /boot/grub2/grub.cfg   # or your bootloader
sudo reboot
#   --> at the GRUB menu pick the just-built kernel, log in, run the TEST,
#       then back in the linux/ dir:
git bisect good      # if resume is CLEAN
#   or
git bisect bad       # if the cascade appears
# repeat (~12 iterations for a merge window). At the end:
git bisect log > ~/i915-bisect.log
git show $(git rev-parse refs/bisect/bad) | head -40
git bisect reset
```

### Good/bad TEST protocol (decide each step deterministically)

Boot the test kernel with KMS debug so the verdict is unambiguous, then exercise the failing path:
```bash
# add to the test kernel's cmdline (or set live): drm.debug=0x100
echo 0x100 | sudo tee /sys/module/drm/parameters/debug
# reproduce a real wake: let the panel deep-idle and wake it, or `systemctl suspend` + wake
journalctl -k -b 0 | grep -iE 'PHY A failed|Failed to bring PHY A|mismatch in dpll_hw_state|port_clock \(expected 810000, found 61440\)|flip_done timed out'
```
- **Lines present → `git bisect bad`.**
- **A real resume happened and the grep is empty → `git bisect good`.**
- **No resume occurred → not a data point; reproduce before deciding** (a false "good" derails the whole bisect).

Tip: the host watcher's `~/scripts/check-i915-resume-fix.sh` exit code encodes this (0 = STILL BROKEN/bad). You can wrap it as `git bisect run` only if you can guarantee a resume happened first — otherwise judge manually.

### Reporting

Post to the upstream issue: the `git bisect log`, the `git show` of the first-bad-commit, your `.config` (or `localmodconfig` note), and confirmation that NVIDIA was blacklisted + which kernels you saw good/bad. That + the `drm.debug` capture from `REPORT.md` is a maintainer's dream report.

## Gotchas
- **Skip non-building commits** with `git bisect skip` (mid-merge-window breakage is common).
- **One variable at a time:** same `.config`, same blacklist, same trigger every step.
- **eDP only:** don't attach external displays during the test — keep the repro on the internal panel/pipe A.
- **Time cost:** ~12 build+boot+suspend cycles on the metal. Budget a few hours. The "fastest path" above often makes the full bisect unnecessary.
