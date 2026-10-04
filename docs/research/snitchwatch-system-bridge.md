# Snitchwatch system bridge integration

The selected deployment runs upstream OpenSnitch as root, Snitchwatch's bridge
as a dedicated system account, and the GUI as an authorized desktop user.
No OpenSnitch daemon patch is required: the pinned upstream daemon supports
`Server.Address: unix:///run/snitchwatch/opensnitchd.sock`.

## Ownership

Snitchwatch owns the bridge implementation, systemd socket/service units,
sysusers/tmpfiles definitions, GUI authorization and deployment profiles, and
protocol/permission tests. Its system integration is documented in
`docs/packaging/system-bridge-integration.md` in that repository.

Bazzite-tower consumes a pinned, verified Snitchwatch release. Image glue is
limited to installing that release's assets, selecting the daemon socket
address, and providing readiness/recovery checks. Do not duplicate those
service definitions in this repository or patch the upstream daemon for UDS.

## Transport and authorization

| Endpoint | Owner/mode | Purpose |
| --- | --- | --- |
| `/run/snitchwatch/opensnitchd.sock` | root:root, 0600 | Root daemon gRPC connection; bridge also checks peer UID 0 |
| `/run/snitchwatch/bridge.sock` | root:snitchwatch-ui, 0660 | Authorized desktop GUI connection |
| `/run/snitchwatch-auth/token` | snitchwatch:snitchwatch-ui, 0640 | Existing GUI handshake compatibility |

Systemd opens both socket listeners and passes them to the unprivileged bridge.
The root-owned socket parent is not writable by the bridge or desktop users.
The separate service-owned token directory uses setgid to preserve the GUI
group without adding the bridge account to that group. Membership in
`snitchwatch-ui` grants authority over system firewall decisions.

## Consumer rollout gate

The current image and host remain on the released user bridge and
`DefaultAction: allow`. Do not change the image's address until a system bridge
artifact is published, pinned and installed. The existing readiness command
currently verifies the legacy deployment only.

Before changing the consumer configuration, verify the following on a
disposable Bazzite VM with enforcing SELinux:

1. Both system socket units activate the same unprivileged bridge, including
   after restart and on a headless boot. No daemon TCP listener remains.
2. The real upstream daemon connects to the protected Unix socket and a GUI
   decision completes a real request. Token rotation and reconnection work.
3. A `snitchwatch-ui` member can use the actual packaged GUI; a nonmember
   cannot. Confirm the Flatpak retains the needed group access and its mounts
   cannot replace either socket or token.
4. Stop/disable the legacy user bridge during migration and select the system
   GUI profile explicitly. Preserve a fail-open configuration and console
   rollback path throughout migration.
5. Update readiness checks for the system sockets, system service, root-owned
   release binary and provenance. Cross-account process inspection may require
   a read-only privileged check rather than the legacy per-user check.

Deny-by-default is a separate policy change after those deployment checks.
