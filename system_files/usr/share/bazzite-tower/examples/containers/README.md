# Rootless container templates

These are inert examples, deliberately stored outside Quadlet discovery paths.
They install no service and contain no credential or usable image reference.

For a rootless Quadlet, copy the files you need into
`~/.config/containers/systemd/`, replace the digest and application-specific
paths, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now app.service
```

The example keeps the container unprivileged, read-only, capability-free, and
on a private network. Add only the minimum writable mounts, capabilities, and
published ports that the actual application needs. Keep real secrets in a
user-owned file outside version control; never place them in a unit file.

For Compose, copy `compose/` to a user-owned directory, create the referenced
`secrets/app_secret` with mode `0600`, set `APP_UID` and `APP_GID`, and use a
deliberately chosen Compose-compatible engine. Quadlet is the recommended
rootless persistent-service path. The sample image digest is intentionally
invalid until you substitute a reviewed, immutable image reference.
