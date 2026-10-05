# Snitchwatch system bridge integration

The proposed deployment runs OpenSnitch as root, Snitchwatch's bridge as a
system account and the GUI as an authorized desktop user. October 4 VM tests
proved the real daemon/GUI round trip; publication and rollout remain deferred.

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

Follow-up manifest and integration-doc fixes are prepared in the isolated
`/tmp/snitchwatch-system-bridge-fixes-1def745` checkout. The combined patch is
`output/vm-payload-1def745/source-followup-packaging-unix-docs-20261004.patch`.
Both packaging contract suites passed (14 tests), as did scratch Flatpak
finish/export using the retained GUI. Check report:
`output/vm-payload-1def745/SOURCE-FIXES-20261004.json`.
A clean corrected production source build and release publication remain unproven.

Headless startup passed; unattended request handling remains unresolved. One
unattended HTTP request completed quickly, but that does not prove all requests
avoid delay: without an authenticated GUI, Ask can remain pending until the
daemon's 120-second timeout. OpenSnitch 1.8.0 also exited with `SIGSEGV` in
`nfq_close` after an interrupted pending query; its cause is not established.

## Consumer rollout gate

The repository config selects the legacy user-bridge endpoint and `DefaultAction: allow`.
Do not change the image address until a system bridge artifact is published,
pinned and installed. Existing readiness checks verify only the legacy deployment.

Before changing consumer configuration, verify on a Bazzite VM with SELinux enforcing:

1. Resolve unattended Ask lifecycle handling and verify request completion
   without a GUI, including after restart and headless reboot.
2. Resolve the packaging/build and daemon shutdown findings, publish and pin
   the release, and verify installation/provenance through the image build.
3. Repeat real GUI decisions, authorization, read-only mounts, token rotation
   and reconnection using that release on the target image with SELinux enforcing.
4. Stop/disable the legacy user bridge during migration and select the system
   GUI profile explicitly. Preserve a fail-open configuration and console
   rollback path throughout migration.
5. Update readiness checks for the system sockets, system service, root-owned
   release binary and provenance. Cross-account process inspection may require
   a read-only privileged check rather than the legacy per-user check.

Deny-by-default is a separate policy change after those deployment checks.
