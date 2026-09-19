#!/usr/bin/env bash
set -euo pipefail

# ── Rebrand the ublue-os login-banner identity ──────────────────────────────
# The upstream base bakes /usr/share/ublue-os/image-info.json describing
# ITS OWN image (ghcr.io/ublue-os/bazzite-nvidia-open:stable). The login MOTD
# (/usr/share/ublue-os/motd/env.sh + template.md) reads image-ref/image-branch
# straight from this file with no awareness that this is a derivative image,
# so an unmodified copy tells every login "you are running
# bazzite-nvidia-open" even on a machine that's actually tracking
# ghcr.io/bearyjd/bazzite-tower. Only override the identity fields; the
# fedora-version/base-image-name/version fields remain true statements about
# the underlying base and are left as upstream set them.
image_info=/usr/share/ublue-os/image-info.json
jq \
    --arg name "bazzite-tower" \
    --arg vendor "bearyjd" \
    --arg ref "ostree-image-signed:docker://ghcr.io/bearyjd/bazzite-tower" \
    --arg tag "latest" \
    --arg branch "latest" \
    '."image-name" = $name
     | ."image-vendor" = $vendor
     | ."image-ref" = $ref
     | ."image-tag" = $tag
     | ."image-branch" = $branch' \
    "${image_info}" >"${image_info}.new"
mv "${image_info}.new" "${image_info}"
