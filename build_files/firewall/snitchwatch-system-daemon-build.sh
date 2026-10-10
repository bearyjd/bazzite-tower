#!/usr/bin/env bash
# Build-only Go tooling and two fresh native compilations in an isolated image.
set -euo pipefail
factory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$factory/snitchwatch-system-mode.sh"
[[ "${SNITCHWATCH_BRIDGE:-legacy}" == system && $# == 1 && "$1" != / ]] || exit 1
[[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || exit 1
# Go, protoc and libnetfilter_queue come from pinned, signature-checked Koji RPMs;
# only the remaining build tools are installed live.
bash "$factory/snitchwatch-system-toolchain.sh" install "$factory/snitchwatch-system-daemon-pins.json" /work/toolchain \
    libnfnetlink-devel pkgconf-pkg-config git gcc python3 binutils coreutils diffutils findutils
mkdir -p /work
work=$(mktemp -d /work/snitchwatch-daemon.XXXXXX)
pins="$factory/snitchwatch-system-daemon-pins.json"
readarray -t pin < <(python3 - "$pins" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
for k in ('sourceRepository','sourceCommit','sourceTree','goVersion','protocVersion','libnetfilterQueueVersion'):print(p[k])
PY
)
[[ "$(go version)" == "${pin[3]}" && "$(protoc --version)" == "${pin[4]}" && "$(pkg-config --modversion libnetfilter_queue)" == "${pin[5]}" ]] || {
    go version; protoc --version; pkg-config --modversion libnetfilter_queue
    echo 'Go builder toolchain drifted from reviewed inputs; refusing.' >&2; exit 1;
}
unset GOFLAGS GOWORK GOOS GOARCH GOAMD64 GOEXPERIMENT CGO_CFLAGS CGO_CXXFLAGS CGO_CPPFLAGS CGO_LDFLAGS CC CXX
export GOENV=off GOWORK=off GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 GOAMD64=v1 CGO_ENABLED=1
export GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org GOMODCACHE="$work/go-mod"
export GOBIN="$work/build-tools" GOCACHE="$work/tool-cache"
mkdir -p "$GOBIN"
while IFS=$'\t' read -r module version package expected; do
    go mod download -json "$module@$version" > "$work/tool-download.json"
    python3 - "$work/tool-download.json" "$module" "$version" "$expected" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if (p.get('Path'),p.get('Version'),p.get('Sum'))!=tuple(sys.argv[2:]):raise SystemExit('Go tool module checksum mismatch')
PY
    go install "$package@$version"
done < <(python3 - "$pins" <<'PY'
import json,sys
for p in json.load(open(sys.argv[1]))['tools'].values():print('\t'.join(p[k] for k in ('module','version','package','sum')))
PY
)
export PATH="$GOBIN:$PATH"
git init "$work/base"
git -C "$work/base" remote add origin "${pin[0]}"
git -C "$work/base" fetch --depth=1 origin "${pin[1]}"
git -C "$work/base" checkout --detach FETCH_HEAD
[[ "$(git -C "$work/base" rev-parse HEAD)" == "${pin[1]}" && "$(git -C "$work/base" rev-parse 'HEAD^{tree}')" == "${pin[2]}" ]]
for name in first second; do
    git clone --no-local "$work/base" "$work/source-$name"
    src="$work/source-$name"
    python3 "$factory/snitchwatch-system-daemon-stage.py" check-source --pins "$pins" --source "$src" --patch "$factory/snitchwatch-system-daemon-shutdown.patch"
    git -C "$src" apply --check "$factory/snitchwatch-system-daemon-shutdown.patch"
    git -C "$src" apply "$factory/snitchwatch-system-daemon-shutdown.patch"
    mkdir -p "$src/daemon/ui/protocol"
    (cd "$src/proto"; protoc -I. ui.proto --go_out=../daemon/ui/protocol/ --go-grpc_out=../daemon/ui/protocol/ --go_opt=paths=source_relative --go-grpc_opt=paths=source_relative)
    python3 "$factory/snitchwatch-system-daemon-stage.py" check-source --pins "$pins" --source "$src" --patch "$factory/snitchwatch-system-daemon-shutdown.patch" --patched
    export GOCACHE="$work/cache-$name"
    (cd "$src/daemon"; go build -mod=readonly -trimpath -o "$work/opensnitchd-$name" .) 2>&1 | tee "$work/build-$name.log"
    [[ "$(timeout 5 "$work/opensnitchd-$name" -version)" == 1.8.0 ]]
    ldd "$work/opensnitchd-$name" | tee "$work/ldd-$name.txt"
    if grep -q 'not found' "$work/ldd-$name.txt"; then
        echo 'Native daemon has an unresolved shared library; refusing.' >&2
        exit 1
    fi
done
cmp "$work/opensnitchd-first" "$work/opensnitchd-second"
go version -m "$work/opensnitchd-first" > "$work/go-build-info.txt"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort > "$work/rpm-inventory.txt"
python3 "$factory/snitchwatch-system-daemon-stage.py" stage --pins "$pins" --source "$work/source-first" \
    --patch "$factory/snitchwatch-system-daemon-shutdown.patch" --work "$work" --out "$1" \
    --verifier "$factory/snitchwatch-system-daemon-verify.py"
# Only the explicit overlay is copied into the image: no Go/protoc/plugin/cache.
