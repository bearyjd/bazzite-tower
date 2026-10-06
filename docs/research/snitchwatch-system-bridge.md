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

## Consumer rollout gate

The repository config selects the legacy user-bridge endpoint and `DefaultAction: allow`.
Do not change the image address until a system bridge artifact is published,
pinned and installed. Existing readiness checks verify only the legacy deployment.

Before changing consumer configuration, verify on a Bazzite VM with SELinux enforcing:

1. Resolve default KDE GUI startup and repeat the runtime/authorization
   regression without the guest rendering/style workarounds. Preserve the
   successful conditional tests separately.
2. Resolve the daemon shutdown finding, rebuild/retest with a supported
   SDK/runtime, then publish and pin the reviewed bridge/GUI release and verify
   installation/provenance through the image build.
3. Repeat real GUI decisions, authorization, read-only mounts, token rotation
   and reconnection using that release on the target image with SELinux enforcing.
4. Stop/disable the legacy user bridge during migration and select the system
   GUI profile explicitly. Preserve a fail-open configuration and console
   rollback path throughout migration.
5. Update readiness checks for the system sockets, system service, root-owned
   release binary and provenance. Cross-account process inspection may require
   a read-only privileged check rather than the legacy per-user check.

Deny-by-default is a separate policy change after those deployment checks.
