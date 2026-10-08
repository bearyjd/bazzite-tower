# Snitchwatch system bridge integration

The proposed deployment runs OpenSnitch as root, Snitchwatch's bridge as a
system account and the GUI as an authorized desktop user. October 4 VM tests
proved the real daemon/GUI round trip. October 5 follow-up work adds authenticated
client tracking and a clean GUI source build; publication and rollout remain deferred.

## Ownership

Snitchwatch owns the bridge, systemd units, sysusers/tmpfiles, GUI profiles and
authorization tests; see its `docs/packaging/system-bridge-integration.md`.

Bazzite-tower should install pinned, verified release assets, select the daemon
address and provide readiness/recovery checks. Do not duplicate Snitchwatch's
service definitions here or patch the upstream daemon for UDS.

## Transport and authorization

| Endpoint | Owner/mode | Purpose |
| --- | --- | --- |
| `/run/snitchwatch/opensnitchd.sock` | root:root, 0600 | Root daemon gRPC connection; bridge also checks peer UID 0 |
| `/run/snitchwatch/bridge.sock` | root:snitchwatch-ui, 0660 | Authorized desktop GUI connection |
| `/run/snitchwatch-auth/token` | snitchwatch:snitchwatch-ui, 0640 | Existing GUI handshake compatibility |

Systemd passes both listeners to the unprivileged bridge. Their root-owned
parent is not writable by the bridge or desktop users. A separate service-owned
setgid token directory preserves the GUI group without adding the bridge to it.
`snitchwatch-ui` membership grants authority over system firewall decisions.

## Daemon compatibility

OpenSnitch 1.8.0 with grpc-go 1.32.0 does **not** successfully subscribe using
`unix:///run/snitchwatch/opensnitchd.sock`. Its address parser strips `unix:`,
leaving a slash-containing HTTP/2 authority that the bridge rejects with
`PROTOCOL_ERROR`. A raw authority probe isolated that failure; the real daemon
then subscribed successfully with this configuration:

- `Server.Address: unix:opensnitchd.sock`
- `opensnitch.service`: `WorkingDirectory=/run/snitchwatch`, with `Requires=`
  and `After=` on `snitchwatch-system-bridge-grpc.socket`.

This VM-only workaround retains the absolute root-owned 0600 listener and root-peer check.

## October 4 VM evidence and limits

The VM used cached Bazzite version `44.20260825`, source `75cf7fe`, kernel 7.2.0.
The bridge was rebuilt from `1def745`, hash-verified and run from `/usr/local/bin`
through a VM-only `ExecStart` drop-in; immutable `/usr` installation is unproven.

Evidence is retained locally under `output/snitchwatch-vm.qyLgPFge/evidence/`:

- `daemon-relative-unix.txt`: real daemon subscription and notification stream.
- `socket-authorization.txt`: nonmembers get `EACCES`; GUI members cannot access
  the daemon socket or replace listeners/token. The bridge account cannot
  replace listeners. Actual Flatpak member/nonmember access and read-only mounts
  passed; see `output/snitchwatch-vm.qyLgPFge/flatpak-authorization-results.json`
  and its referenced GUI, direct-socket and mutation logs.
- `token-rotation.txt`: restart changed the bridge PID and token; the GUI reconnected.
- `headless-boot.txt`: a new enforcing boot in `multi-user.target`, with no GUI,
  activated both sockets/bridge; the daemon subscribed and opened notifications before SSH.
- `real-rendered-gui-verdict.txt`, `actual-allow-click.txt` and the before/after
  GUI screenshots: clicking Allow for `/usr/bin/curl` to `10.0.2.2:49187` changed
  row `ask-1` to allowed. The daemon logged a new rule at 23:41:07 UTC and the
  unique HTTP fixture completed. SELinux was enforcing; no matching AVCs were
  found in the scoped test window.

The GUI was a source-built KDE 6.9 Flatpak with VM-only repairs: install the
missing SVG icon and replace unsupported `xdg-state` with app-owned persistence.
The VM patch at `output/vm-payload-1def745/system-profile-packaging-fixes.patch`
left the external home checkout untouched. Mold/protoc setup and software Qt
rendering were used; the offline test omitted the optional GL extension.
The original production manifest's standalone build failed.

The manifest and Unix integration-document fixes were subsequently committed
as Snitchwatch `2690109`. The retained report
`output/vm-payload-1def745/SOURCE-FIXES-20261004.json` covers 14 packaging
contract tests and scratch finish/export of the previously compiled GUI; that
check alone did not establish a clean production source build.

The original unattended HTTP sample was inconclusive: another pending Ask
could trigger the daemon's busy fallback. At `1def745`, Ask without a GUI could
wait for the daemon's 120-second timeout. The original guest also observed
OpenSnitch 1.8.0 `SIGSEGV` in `nfq_close` after an interrupted pending query.
The daemon shutdown finding remains a rollout blocker.

## October 5 reviewed follow-up

The validated build used an isolated source checkout based on committed
`2690109` with reviewed runtime and build-tool changes that were uncommitted
at build time. The exact combined source snapshot
is SHA-256 `851f3a5647c373fe88477b0dddb63b5bc980084611f1d0b934f5ae82dd50cf07`;
this is a snapshot hash, not a new source commit. All 783 exported source files,
including the pinned OpenSnitch gitlink `b404c4c`, were independently checked
against the frozen snapshot before and after compilation. Subsequent source
integration/build-document edits are documentation only and are recorded
separately in `.omc/state/snitchwatch-rollout-docs.json`.

The bridge now tracks external WebSocket clients only after token validation
and a successful authentication acknowledgement. Internal subscribers do not
count as a GUI. Without an authenticated client, an unpaused Ask returns gRPC
`Unavailable` before inserting a pending row. Losing the last client cancels
existing pending requests even if another client reconnects immediately. RPC
cancellation removes its pending row and late verdicts cannot create rules or
history.
The daemon retains its configured fallback action; the bridge does not copy or
reinterpret policy. Paused auto-allow and the deadline for an authenticated but
silent GUI are preserved.

A fresh x86_64 production system-profile build passed with KDE SDK/Qt 6.9.3,
Rust 1.89.0, declared protoc 29.3 and mold 2.40.4. The pinned Cargo generator
verified 657 crates against the unchanged lockfile. No compiled GUI target,
external compiler adapter or developer Cargo configuration was reused. The
export removes protoc and its debug file; both profiles retain their prior
permissions. System metadata is byte-identical to the previously validated
Flatpak profile. Evidence and full hashes:
`output/snitchwatch-production-20261004/CLEAN-PRODUCTION-PROVENANCE.json`.
The local bundle is `snitchwatch-clean-system.flatpak`, app commit `83f1053`;
release publication and immutable image installation remain unproven. Guest
installation (`new-gui-install.txt`) reported the retained KDE 6.9 runtime as
end-of-life. This build proves compilation in that pinned environment; release
validation must move to a supported SDK/runtime and repeat the checks.

New guest evidence is retained under
`output/snitchwatch-rollout.zo4v4fci/evidence/`, using native bridge hash
`ff467122` with `DefaultAction: allow` and enforcing SELinux. Limits were
fixed before execution in `acceptance.json`: five seconds for request/fallback
completion, two seconds for pending cleanup, and 15 seconds for daemon stop.

- `new-native-no-gui.txt`: no authenticated GUI/helper, no other pending Ask,
  and a novel unmatched destination. The exact curl reached Ask, received
  `Unavailable`, and the daemon applied its default action. HTTP completed in
  20.090 ms, with pending count zero before and after.
- `new-native-last-ws.txt` and its daemon log: the sole authenticated external
  WebSocket helper disconnected while its exact curl was pending. HTTP
  completed in 15.388 ms; a fresh snapshot returned to zero pending within
  60.5 ms of disconnect. This sample uses a WebSocket helper rather than a
  rendered GUI; the separately verified rendered-GUI case follows below.
- `new-native-headless-boot.txt` and `new-native-headless-no-gui.txt`: a fresh
  enforcing boot into `multi-user.target`, without a GUI, activated the bridge
  and daemon notification stream. Another exact unmatched curl received
  `Unavailable` and daemon fallback, completed in 10.971 ms, and left no
  pending row.
- `new-native-token-rotation.txt`: bridge restart changed the token, closed the
  old helper session and rejected its stale token. A fresh authenticated helper
  snapshot and ping/pong succeeded within 81.434 ms. The separately verified
  rendered GUI re-authentication is recorded below; this timing belongs to the
  helper check.
- `new-native-rpc-cancel.txt`: a synthetic root-peer request over the protected
  daemon IPC endpoint canceled only its tonic RPC while retaining the channel.
  Its pending row disappeared within the two-second budget (recorded as less
  than 1 ms at integer resolution), and a late verdict had no rule/history
  effects. This tests IPC cancellation, not real outbound HTTP traffic.
- `new-native-stop-resolved.txt`: an earlier resolved-request daemon stop exited
  cleanly within the 15-second budget with no coredump and an empty OpenSnitch
  nftables table. This remains a narrow historical sample; the final resolved
  control after actual rendered Allow retained warnings, as recorded below.
- `new-native-stop-pending.txt`: stop returned in 5.170 seconds with service
  `Result=success`, exit/status zero and no core. The original authenticated
  WebSocket remained open while the pending row was removed; its snapshot
  showed zero pending 11.036 ms after stop returned. Restart restored Subscribe
  and exact no-GUI HTTP completion in 1.847 seconds. Both queue watchdog timeout
  warnings and `nfq_close() not closed: -1` remain in this sample. Successful
  process exit does not establish safe queue shutdown or resolve the crash race.
- `new-native-stop-interrupted.txt`: curl PID 7706 was terminated with SIGTERM
  after its exact Ask was observed; the same row remained pending before stop.
  Stop returned in 211.981 ms with service success/status zero, no core and no
  watchdog warning in this sample. The original WebSocket stayed open through
  zero-pending verification 9.201 ms after stop returned. Restart restored
  Subscribe and exact no-GUI fallback HTTP completed in 12.073 ms. This narrow
  successful sample does not negate the pending-case warnings or historical
  shutdown crash.
- `new-gui-last-disconnect-auto*`: the exact `83f1053` rendered GUI was the
  sole authenticated client while real curl PID 6947 was pending. The admission
  helper closed before the automated kill marker. The exact Flatpak instance
  was verified gone within 23.932 ms of kill initiation; pending returned to
  zero within 74.691 ms and HTTP 200 completed within 75.970 ms. Daemon evidence
  identifies that curl's last-session `Unavailable` and configured fallback.
  This conditional GUI test used all four overrides below. The two earlier
  manual coordination attempts are excluded from timing acceptance.
- `new-gui-reauth-journal.txt`: the same rendered GUI survived bridge restart
  and token hash change, then logged connection after a fresh authentication
  acknowledgement. This proves actual GUI re-authentication in the conditional
  environment; it does not inherit the helper's 81.434 ms timing.
- `new-gui-rendered-allow*`: independently reviewed before/after screenshots
  and a QMP click on the rendered Allow button changed exact curl row `ask-1`
  for `10.0.2.2:49188` from pending to allowed. The observer sent no verdict.
  The daemon logged the matching new allow rule, HTTP 200 completed in
  521.005 ms, and the authoritative snapshot showed the matched allowed row
  with zero pending. This uses the same four conditional overrides.
- `new-gui-authorization-summary.txt` and direct-socket evidence: both tested
  accounts saw read-only IPC/auth mounts and denied mutation attempts; the
  outsider's direct socket connection returned `EACCES`. In
  `new-gui-outsider-actual-binary.txt`, the exact reviewed GUI binary ran as
  UID/GID 1001 without the GUI group, retried token reads five times with
  `EACCES`, and never logged a connection. Its five-second bound ended with the
  expected timeout status 124. This offscreen/software/Basic/generic denial
  probe retains QtWidgets/portal warnings and does not prove default startup.
- `new-gui-resolved-stop-and-vm-finish.txt`: after actual rendered Allow, the
  exact GUI instance was stopped and a snapshot showed two decided allow rows
  with zero pending. The resolved-request daemon stop returned in 5.157 seconds
  with success/status zero and no core, but retained one queue watchdog warning
  and `nfq_close() not closed: -1`, plus
  `nftables: error applying changes: netlink receive: operation not permitted`.
  This final resolved control reinforces the unresolved shutdown gate rather
  than the earlier clean sample, even with no live Ask.

`new-native-audit-result.json` records no AVC matches in the full kernel
journal and `ausearch` for the captured current guest boot. This scoped result
does not establish general SELinux compatibility.

Final cleanup stopped the guest bridge and both socket units, then powered off
the disposable VM. `HOST-CLEANUP-20261005.json` records the VM process absent
and owned test ports closed; only the task-owned HTTP fixture was terminated.
The original VM evidence and unrelated VMs were preserved.

The new bundle's first KDE launch exited with status 134. Its full application
journal records Breeze ToolTip loading followed by `QWidget: Cannot create a
QWidget without QApplication`; the GUI creates a `QGuiApplication`. Evidence:
`output/vm-payload-1def745/clean-gui-library-core-diagnosis-20261005.log`.
A Basic-controls/software-rendering attempt also exited 134 through
`Qt.labs.platform` and `KF6StatusNotifierItem` into QtWidgets. The reviewed
bundle rendered with all four guest-only overrides:
`QT_QPA_PLATFORM=wayland`, `QT_QUICK_BACKEND=software`,
`QT_QUICK_CONTROLS_STYLE=Basic` and `QT_QPA_PLATFORMTHEME=generic`
(`new-gui-generic-wake.png`). Conditional GUI regression uses that environment;
it does not establish default KDE startup. This remains a new rollout blocker
with no source fix in this validation. The read-only proposed application fix
is recorded in `output/vm-payload-1def745/GUI-QAPPLICATION-PROPOSAL-20261005.md`.

These conditional runtime and authorization checks apply to the exact new
artifacts. They leave default KDE startup, daemon queue shutdown, runtime
lifecycle and release/image integration unresolved.

## Opt-in image integration candidate

The initial image plan used reviewed Snitchwatch commit `d09defc`. The
reconciled candidate pinned `5c2b44adece96008e973947a9551b700b8d8a15b`, tree
`fa952a8c2547160e928c1ee9b81be80370ef822e`; since 2026-10-07 it pins
`670f42c28ace7cbffe6b032f6aa72b0f2bb2414c` (tree `0a52b15a…`, Snitchwatch PR
#39: #47 pause fixes and #44 app-bound prompt rules), and OpenSnitch submodule
`b404c4c6316760fa7bc415509d3f8d747f7dc9cc`. A fresh Fedora 44 native factory
build produced actual CLI version 0.1.1; a second fresh source/target build
reproduced the artifact bytes. The Fedora 43 binary used by the October 5
disposable VM and the divergent published v0.1.1 user-service release are
not image deployment inputs. Original schema1 artifact evidence and the
installed system-overlay provenance remain separate.

`SNITCHWATCH_BRIDGE=legacy` remains the default. The explicit `system` candidate
requires `FIREWALL_DAEMON=opensnitch`, installs the native bridge and system
units, and selects `unix:opensnitchd.sock` with OpenSnitch's working directory
set to `/run/snitchwatch`. Both profiles preserve `allow`/`proc`. GUI delivery
remains per user with explicit system-profile selection and group enrollment.
CI's third PR verification leg uses only a local `verify-snitchwatch-system`
tag and a read-only token; the two publishing release variants remain legacy.

October 6 build evidence at the exact reconciled source:

- Native bridge artifact SHA256: `5b1c89864985b862c2782dd7ac340aeefe7ba29f71de7bed3ede1b0e8039c2d1`; actual 0.1.1 binary SHA256: `cce18095907a0364abcae6f0be7c3e3b3554c982827ac2b3e9a11fe48e016370`. Artifact, checksum sidecar, licenses, source/tree/gitlink and 20 immutable overlay files passed independent inspection.
- Fresh system-profile GUI bundle SHA256: `ab7d6ee8aefc195178a7914d343780a3a01b5c1b7512ba363a5ecf025d696ae0`. Its clean release build used supported KDE 6.11 / Qt 6.11.2, Rust 1.98.1, mold 2.42.0, declared protoc 29.3 and 657 lockfile-verified crate inputs. The GUI crate version remains 0.1.0. A separate archive supplies the full committed workspace, exact upstream vendor source, complete crate source archives and license texts.
- The reviewed downstream OpenSnitch repair applies to upstream 1.8.0 at `b404c4c`. It cancels UI requests, joins both queue readers and callbacks while firewall hooks remain valid, then removes the hooks and releases the queues. Independent targeted race tests, the actual C callback fixture, full normal package tests, NFT ownership cases and a real watchdog child exiting nonzero passed. The October 6 16-file revision (`6e48804a…`, binary `3ea27d30…`) silently lost a queue reader on some boots; see [Target-image validation](#target-image-validation-october-67). The 19-file patch of commit `d96a7a5` (`8d68ad9e…`, binary `2cf22351…`) fixed that. The 22-file patch (SHA256 `4e7d9fe6…`, binary `8c925640…`, the r4 image) adds two fixes. It drains replies the reader never read before `nfq_destroy_queue()` (see [the investigation](opensnitch-nfq-destroy/README.md)). It also hands queued packets to the workers as soon as the queue exists, rather than after the rest of startup. The current 26-file patch (SHA256 `4419cc2e5b4da0f5e01c804701bab736af490465829c34285f5498de42dc43a9`) also closes the repeat-queue hand-off race (see the open items). A local pair of builds is byte-identical at `380540065ea93cb10b8071a1480a17aba1a70f88fdae608c863d22738c6bb881`; it has not been built into an image or run on a VM.
- The earlier patch `4b53c88c390a0e85cb17067532bf6232034bd5f3f2e0843e5dc1a988dcc88043` and binary `2e6daa72db1e14b7ef3b82b7a04ef1ca4acd3f441c934ec4b9c4ae0c7310899b` are retained historical diagnostics. Fast stops still logged `nfq_destroy_queue() not closed: -1` and canceled-Ask invalid-rule errors. Bounded, nonconsuming instrumentation identified a stale negative ACK for a VERDICT preceding the successful UNBIND configuration ACK. Those instrumented binaries are excluded from shipping and do not prove the new uninstrumented repair passes on a VM.
- A broader full-package race run failed in UI configuration watcher/global state paths. The same failure reproduced on unchanged upstream `b404c4c` with identical tools and generated protocol inputs; existing tests create successive clients without watcher cleanup. The scoped shutdown regressions passed independently. This does not establish production configuration reload paths are race-free; both failure logs and the unchanged source archive are retained.

Source/artifact approval and causal observations are retained under
`output/snitchwatch-daemon-diagnostic.qljb0f1g/`, including
`INDEPENDENT-FOLLOWUP-FINAL-REVIEW.json` and
`INDEPENDENT-UPSTREAM-RACE-BASELINE-RETENTION.json`. The image factory and
installed daemon inventory have a separate manifest from the Rust bridge.

The October 4–5 measurements above remain historical artifact results.

## Target-image validation (October 6–7)

Both candidates were built as clean, native rootful factory images (two Rust and
two Go builds each, reproducible) and booted from fresh disposable qcow2 disks
under enforcing SELinux. Local images `38851820`/`0793d4a4` were rejected
earlier (the October 6 session recorded malformed RPM SQLite databases); neither
rebuild shows that defect (SQLite integrity, `--verifydb` and the 2998-package
inventory agree).

**`78be87b` (image `5b6b9a7e`) failed.** First boot, the protocol probes and the
warm no-GUI fallback passed, but the cold multi-user gate did not: in 2 of 7
boot-time daemon starts opensnitchd stopped verdicting. The kernel kept queueing
(`/proc/net/netfilter/nfnetlink_queue` backlog 77→107→113 in one stalled boot,
65→68→71 in the other) while the daemon stayed bound, so `QueueBypass` never
applied and new outbound connections hung. Goroutine dumps showed one of the
two `netfilter._Cfunc_Run` readers missing and every worker idle, and the
reader had exited with `EIO`. The patch's C `Run()` returned `EIO` whenever
`nfq_handle_packet()` failed, which upstream ignores. The most likely trigger
is an `NLMSG_ERROR` reply to a verdict for an entry the kernel flushed when
another base chain was unregistered at boot (libnetfilter_queue reports any
`NLMSG_ERROR` as a failure); that trigger was inferred, not reproduced on the
unfixed daemon. The exit message had no newline, so journald surfaced it only
at process exit, and nothing restarted the daemon.

**`d96a7a5` repairs it**: `Run()` ignores per-message failures as upstream does,
a reader that still stops without a stop request triggers the normal shutdown
path with exit status 1 so systemd restarts the daemon, and the receive buffer
holds a full 4096-byte copy-range message (a truncated message never gets a
verdict and can fill the queue). The C reader fixture failed before each C
change (retained red runs); independent code and security reviews approved the
final patch.

**`d96a7a5` (image `63da3c01`) passed** every gate run on the VM:

- first boot: exact image identity, named accounts, empty UI group, 0 AVC;
- probes: identity, DAC, fixture, warm and cold no-GUI fallback (HTTP 200 in
  9 ms, pending 0), eight timed daemon stops (~0.2 s each, strict
  warning-free predicate, foreign NFT and firewalld preserved), foreign-NFT,
  migration, refusals and token rotation;
- 15 consecutive cold multi-user boots with no stalled queue, and 5453 nft
  base-chain register/unregister cycles under traffic with the same daemon
  PID, 536/536 connections answered and no reader stop;
- default KDE GUI with no Qt overrides (Wayland, ELF `eb2ad207…`, app commit
  `eea1d608…`): a novel connection held as pending and released by its inline
  Allow (HTTP 200 about 71 ms after the click); a bridge restart rotated the
  token and the same GUI process reconnected and decided a new prompt; killing
  the sole GUI instance fell back to the default action (HTTP 200 0.091 s after
  the kill, exact `last authenticated GUI session disconnected`); an unenrolled
  account got `EACCES` on the token and never authenticated; root-only gRPC
  stayed `EACCES` after enrollment; 0 AVC on the GUI boot.

The positive-Allow result holds on a quiet system only: four earlier attempts
were default-allowed without a prompt because an unrelated avahi, chronyd or
systemd-resolved prompt was already pending. The Allow and reconnect timings
were observed during the session and transcribed (guest files stayed on the
VM disk). Pending cleanup within 2 s after GUI loss is inferred from the
`Unavailable` reply and curl completing 0.091 s after the kill; the
authoritative pending-0 snapshot was taken later.

Not exercised on the VM: the new reader-death fallback (unit-tested only; the
VM shows the trigger no longer kills the reader, not the fallback itself), a
late verdict for a removed row (the helper timed out before finding a pending
row, so no verdict was sent), a GUI Deny, cold-boot stall checks on `graphical.target` beyond one
GUI boot, and recorded identity of the unenrolled account's binary.

VM-only fixture steps, recorded with the evidence: BIB writes the test user's
home as `default_t`, so it was relabelled before key SSH; a refusals fixture
left an empty `/etc/systemd/system/opensnitch.service.d` that readiness
rejected, so the harness now removes it; for the GUI tests avahi, chronyd and
tailscaled were stopped and one rule allowed only the VM DNS server
`10.0.2.3`, because opensnitchd serializes prompts and an unrelated pending
prompt makes it default-handle the test connection silently.

Evidence: `output/snitchwatch-fresh-vm.u4luhx70/COLD-NO-GUI-GATE-FAIL.json`,
`STALL-ROOT-CAUSE-EVIDENCE.json` and
`output/snitchwatch-fresh-vm-r3.KLkez3/R3-VM-ACCEPTANCE-RESULT.json`.

Open items:

- `nfq_destroy_queue() not closed: -1` appeared 3 times in the daemon log,
  each in the stop just before a stall-loop reboot (observed, not exported);
  the eight timed daemon stops were warning-free. Root cause found and fixed
  2026-10-07, reproduced against the real kernel: a stale verdict error for an
  entry the kernel flushed when another service removed a base chain was read
  by `nfq_destroy_queue()` as its unbind reply. The queue was in fact
  unbound. See [opensnitch-nfq-destroy](opensnitch-nfq-destroy/README.md). The
  fix drains pending replies first. Confirmed on the r4 VM: a scripted
  probe (a held Ask, a base-chain flush, then a stop) warned in 18 of 20
  rounds on r3 and 0 of 20 on r4. Ordinary stops and 10 graphical cold
  reboots on r4 logged none.
- At first boot the patch's 1 ms `deliverPacket` timeout accepted one packet
  without a decision (`Timed out while sending packet to queue channel 2`,
  0.6 s after the daemon loaded its rules on r3). Channel 2 is the **repeat
  queue** (queues are numbered in creation order, primary first). Likely
  mechanism, not yet proven: when the daemon asks the GUI, a worker re-queues
  the packet there and then waits on the repeat channel; the repeat queue's
  reader gives that hand-off only 1 ms, and under first-boot CPU load the
  worker can arrive later, so the packet is accepted without being asked
  about. Fixed on 2026-10-07 in the 26-file patch: the repeat queue now waits
  up to `repeatHandoffTimeout` (1 s), the same budget the asking worker
  waits, so its reader can no longer give up first while the worker is on its
  way. The same change gives any stale requeued packet a verdict (before, a
  mismatch left the repeat reader blocked forever) and claims the single
  prompt slot atomically (`TryStartAsking`). Unit tests prove the hand-off
  mechanism in isolation, each checked by a mutation; the r3 first-boot
  incident itself was not reproduced. On the VM (r5, same stress script,
  every core saturated, no GUI so each Ask takes the hand-off) r5 logged 0
  channel-2 timeouts and 0 lost requeues in 900 attempts; the r4 control
  logged 3 in 900.
  The primary queue keeps upstream's 1 ms. It was mis-attributed on 2026-10-07 to a separate
  startup window, which the patch now closes as hardening (owner's choice of
  option). The queue's reader started in `setupQueues`, but packets reached the
  workers only after UI connect, rule reload, process-monitor and DNS setup.
  Dispatch now starts with the queue. Neither r3 nor r4 produced a timeout in
  10 daemon restarts under connection load, so that window is small in
  practice. The 1 ms accept-on-timeout still applies when all workers are
  busy.
- ~~A GUI Allow creates a destination-only rule.~~ Fixed in Snitchwatch
  `670f42c` (#44): a remembered "This host" answer now writes `list` =
  `process.path` (sensitive) AND `dest.ip`/`dest.host`; VM-confirmed on r5.
  Inline row buttons still answer "once", and existing host-only rules are
  not migrated.
- The GUI's inline Deny did not stop a retrying connection on r4. The daemon
  logged `Added new rule: deny if dest.ip is 127.0.0.5`, yet the denied curl
  completed (HTTP 404, i.e. allowed) about 4.4 s after the click, 19.4 s
  after it started; a repeat curl timed out after 8 s. Root cause, from
  source and confirmed on r5: the daemon never stores a `once` rule
  (`rule/loader.go` `addUserRule`), and "Added new rule" is logged anyway. So
  inline Deny drops one SYN; the retransmit asks again and, while that Ask
  holds the only prompt slot, other new connections get the default allow
  (r5: a fresh python request returned 200 in 0.02 s). A sheet Deny with
  "Until quit" stored an app-bound in-memory rule that refused retries
  without prompting and was gone after `systemctl restart opensnitch`. Owner
  decision: inline Deny becomes "until restart", app-bound (Snitchwatch side).
- ~~Readiness treats an empty `opensnitch.service.d` directory as a local
  override.~~ Fixed 2026-10-07. Under a root-only unit directory
  (`/etc/systemd/system`, `/run/systemd/*`, `/usr/local/lib/systemd/system`),
  an empty `.d` directory is no longer refused if it is a real directory,
  owned `root:root` and not group- or world-writable. The decision comes from
  one `lstat`. Still refused: any entry in it, a symlink, a non-directory,
  wrong ownership, a writable directory, and any `.d` under a home directory,
  because the user owns its parent. Removing that refusal also dropped an
  accidental tripwire: a loaded drop-in deleted without `daemon-reload`
  leaves an empty directory. So readiness now requires
  `NeedDaemonReload=no` for the bridge service, both sockets and
  `opensnitch.service`. That also catches a drop-in added in a directory the
  scan does not list, such as `system.attached` or dash-prefixed names.
  Tested in `tests/test-snitchwatch-system.py`.
  The two security-review leftovers are fixed (2026-10-07): readiness now
  pins `opensnitch.service` to `/usr/lib/systemd/system/opensnitch.service`
  with exactly the `20-system-bridge.conf` drop-in (plus, at most, Fedora's
  byte-pinned `service.d/10-timeout-abort.conf`), and a path it cannot inspect
  (EACCES, ELOOP, EIO) is refused instead of treated as absent. The pinned
  values match the r5 VM's `systemctl show`, and the hardened helper passed
  readiness there.
- Rule names from the UI channel reached root file paths unvalidated
  (`../default-config` made the daemon write or delete outside
  `/etc/opensnitchd/rules`). Fixed in both layers on 2026-10-08: the bridge
  (Snitchwatch #57) and, as defence in depth, the 32-file daemon patch, which
  validates names in `Add`, `Replace`, `Delete` and `deleteRuleFromDisk` (no
  separators, control, format or line-separator characters, at most 200
  bytes). The daemon's `CHANGE_CONFIG` notification could also move
  `Rules.Path`, the log file, server address, TLS and other paths (not
  reachable: the bridge only sends `CHANGE_RULE`/`DELETE_RULE`, guarded by a
  Snitchwatch test). The patch now accepts a UI-sent config only when it
  differs in UI-tunable settings (DefaultAction/Duration, InterceptUnknown,
  logging, stats, internal tuning, rule checksums, monitor interval).
- ~~The daemon factory and CI do not run the patch's Go tests.~~ Fixed
  2026-10-07: `just test-snitchwatch-daemon-patch` and the path-filtered
  `snitchwatch-daemon-patch.yml` workflow run them, `-race` included.
  Putting back the r2 `return EIO` makes the gate fail
  (`.agent_native/agent_roadmap.md` item 7).
- Ignored per-message `nfq_handle_packet()` failures are not counted or
  logged; the `NETLINK_NO_ENOBUFS` `setsockopt` result is unchecked; reader
  errors name the internal queue index, not the queue number.
- Serialized prompts are an upstream limitation with a real desktop cost:
  background services can hold the only prompt slot. On r4 a `kioworker`
  Ask (discord.com) held it right after login; Steam and the Bazzite welcome
  app were also closed to clear the queue.

## Consumer rollout gate

Default rollout requires the following, even if the opt-in image candidate
passes its scoped integration checks:

1. Repair default KDE GUI startup and repeat GUI authorization/decision checks
   without the four documented conditional overrides.
2. Resolve OpenSnitch queue teardown and NFT warning behavior, then validate
   the GUI on a supported Qt SDK/runtime.
3. Review the new native release/source/toolchain/license and installed-overlay
   provenance; validate cold first boot and exact novel unmatched Ask fallback
   on the target Bazzite image with SELinux enforcing.
4. Preserve a recorded legacy baseline during transactional migration and
   rollback. Reject live `/etc` policy/address drift, user-unit overrides,
   conflicting legacy listeners and incompatible same-ID Flatpak profiles or
   overrides. Keep `allow` and a console recovery path throughout.
   Serialize policy writers with the helper's migration lock. An unexpected
   config displaced at publication is retained in the protected journal for
   manual recovery; the daemon stays stopped and automatic rollback refuses.
   Arbitrary uncoordinated root writes cannot be strictly compare-and-replaced.
5. Verify selected-profile readiness and controlled GUI Allow, authorization,
   read-only mounts, token rotation, reconnection and stop/rollback evidence.
   Account IDs must come from the target image's named accounts rather than
   the earlier VM's numeric IDs. No user receives GUI membership implicitly.

Status for the `65e02dd` (r4) candidate in the disposable VM (2026-10-08 UTC):
gate 1 is met for default KDE startup, Allow and Deny decisions (Deny with the caveat above), last-GUI
loss and late-verdict rejection, without overrides on KDE 6.11. Gate 2 is met for teardown: the `nfq_destroy_queue`
warning is fixed and VM-confirmed, and a forced reader death logged, exited 1,
was restarted by systemd after 30 s and let traffic through meanwhile
(QueueBypass; 44 of 45 requests succeeded). Gates 3–5 were re-run on r4: independent image review, cold
first boot, every acceptance probe phase, readiness drop-in handling, 10
graphical cold boots with no stall. A host trial and the open items above
remain before any default rollout. Evidence:
`output/snitchwatch-fresh-vm-r4.G9c5CJ/R4-VM-ACCEPTANCE-RESULT.json`, with r3
controls in `output/snitchwatch-r3-control.*/`.

Status for the `fc418c2` (r5) candidate (26-file daemon patch, Snitchwatch
`670f42c`) in the disposable VM (2026-10-08 UTC): independent image review
PASS; cold first boot and every acceptance probe phase PASS; readiness
drop-ins, pending-flush-stop (0/20), startup window (0/10) and teardown
stress (0/20) unchanged from r4; 10 graphical cold boots clean; repeat-queue
stress 0/900 against r4's 3/900; app-bound Allow, #47 pause (auto-allow only
with a GUI, cleared when the last GUI leaves, not inherited), last-GUI loss
(0.076 s) and late-verdict rejection PASS; 0 AVC, no core dumps. Reader death
was not re-run (r4 PASS, code path unchanged). Evidence:
`output/snitchwatch-fresh-vm-r5.N3rERm/R5-VM-ACCEPTANCE-RESULT.json`.

The fixed target-image acceptance limits are 5 seconds for no-GUI fallback,
2 seconds for pending cleanup and 15 seconds for daemon stop. Preserve the
shutdown warnings even when exit status and stop timing pass. A container
boot cannot establish NFQUEUE interception or enforcing-SELinux VM behavior.
Deny-by-default remains a separate policy change after these deployment gates.
