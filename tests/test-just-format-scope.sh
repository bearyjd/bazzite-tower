#!/usr/bin/env bash
# Offline test: `just format` must only hand build_files/, scripts/ and installer/ shell files to shfmt.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
mkdir "${work}/bin"
cat > "${work}/bin/shfmt" << EOS
#!/usr/bin/env bash
echo "\$*" >> "${work}/argv"
EOS
chmod +x "${work}/bin/shfmt"

(cd "${repo_root}" && PATH="${work}/bin:${PATH}" just format > /dev/null 2>&1)

[[ -s "${work}/argv" ]] || { echo "FAIL: shfmt was never called" >&2; exit 1; }
bad=$(grep -v -E -- '(^| )\./(build_files|scripts|installer)/' "${work}/argv" || true)
if [[ -n "${bad}" ]]; then
    echo "FAIL: shfmt invoked outside build_files/scripts/installer:" >&2
    echo "${bad}" >&2
    exit 1
fi
echo "PASS: just format limited to build_files/, scripts/, installer/"
