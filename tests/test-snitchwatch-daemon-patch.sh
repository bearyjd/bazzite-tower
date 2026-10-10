#!/usr/bin/env bash
# Apply the pinned downstream OpenSnitch patch to its pinned upstream commit and
# run the patch's Go tests (with -race) and its C harness tests in throwaway
# containers. -race covers Go code; the C harness binaries are built by the
# tests themselves without a sanitizer.
#
# The daemon factory (build_files/firewall/snitchwatch-system-daemon-build.sh)
# builds the patched daemon but never runs its tests: the October 6 reader-exit
# stall shipped in a reviewed patch and was caught only by the VM cold-boot gate.
# See .agent_native/agent_roadmap.md item 7.
#
# Two phases, both from the pins' builderImage digest:
#   prepare  (network)       download the toolchain RPMs, refuse toolchain drift,
#                            fetch + verify upstream, apply the patch through the
#                            factory's own check-source gates, generate the gRPC
#                            protocol, download Go modules.
#   test     (--network=none) install the same RPM files offline, re-check the
#                            toolchain, then gofmt/vet/test with GOPROXY=off.
#
# Exit status: 0 pass; 3 toolchain drift (the Fedora builder moved away from
# the reviewed pins, not a test failure); any other non-zero is a failure.
set -euo pipefail

FACTORY_FILES=(snitchwatch-system-daemon-pins.json snitchwatch-system-daemon-shutdown.patch
    snitchwatch-system-daemon-stage.py)
# Must equal the dnf list in snitchwatch-system-daemon-build.sh (checked in host).
TOOLCHAIN_RPMS=(golang libnetfilter_queue-devel libnfnetlink-devel pkgconf-pkg-config
    protobuf-compiler git gcc python3 binutils coreutils diffutils findutils)
# Upstream b404c4c's entire ui test suite. On the pristine commit these fail
# under -race too (data races in upstream ui code; TestClientDefaultConfig
# intermittently, verified 2026-10-07), so they are skipped only in the -race
# pass and still run in the plain pass.
UPSTREAM_UI_RACE_FAILURES='^(TestClientDefaultConfig|TestClientInvalidProcMon|TestClientReloadingConfig)$'
# Upstream rule test that loads a blocklist directory against a 5 s deadline;
# it times out under -race when the host is loaded (passes 5/5 when idle).
# It still runs in the plain go test pass below.
UPSTREAM_RULE_RACE_FLAKES='^TestNewOperatorListsSimple$'
# Upstream vet findings in the root and ui packages (an unbuffered
# signal.Notify channel; protobuf messages copied by value; unkeyed protobuf
# literals). Every other analyzer still runs on the patched packages.
UPSTREAM_VET_FINDINGS=(-sigchanyzer=false -copylocks=false -composites=false)

work=     # host-side scratch dir; global so the EXIT trap can still see it
names=()  # host-side container names, removed by the EXIT trap

in_container() {
    [[ -e /run/.containerenv || -e /.dockerenv ]] && [[ ! -e /run/.toolboxenv && ! -e /run/host ]] || {
        echo "$1 must run inside a throwaway container" >&2
        exit 1
    }
}

# Network steps only: a transient registry or proxy error must not read as a
# test failure.
retry() {
    local attempt
    for attempt in 1 2 3; do
        "$@" && return 0
        echo "attempt $attempt failed: $*" >&2
        sleep $((attempt * 10))
    done
    return 1
}

pin() {
    python3 - /work/factory/snitchwatch-system-daemon-pins.json "$@" <<'PY'
import json, sys
pins = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    print(pins[key])
PY
}

install_toolchain() {
    # Local files skip OpenPGP checks by default; the builder image already
    # trusts the Fedora key, so check them as a repository install would.
    dnf -y --setopt=install_weak_deps=False --setopt=localpkg_gpgcheck=True --disablerepo='*' install /work/rpms/*.rpm
    local go_version protoc_version nfq_version want_go want_protoc want_nfq
    # Resolved before the comparison so a pins error fails here, not as drift.
    want_go=$(pin goVersion)
    want_protoc=$(pin protocVersion)
    want_nfq=$(pin libnetfilterQueueVersion)
    go_version=$(go version)
    protoc_version=$(protoc --version)
    nfq_version=$(pkg-config --modversion libnetfilter_queue)
    if [[ "$go_version" != "$want_go" || "$protoc_version" != "$want_protoc" || "$nfq_version" != "$want_nfq" ]]; then
        printf 'TOOLCHAIN DRIFT (not a test failure): %s | %s | libnetfilter_queue %s\n' \
            "$go_version" "$protoc_version" "$nfq_version" >&2
        exit 3
    fi
}

prepare() {
    in_container prepare
    # dnf5: --resolve fetches every dependency the base image lacks; --arch
    # keeps i686 multilib out.
    retry timeout 300 dnf -y --setopt=install_weak_deps=False download --resolve --arch=x86_64 --arch=noarch \
        --destdir=/work/rpms "${TOOLCHAIN_RPMS[@]}"
    install_toolchain
    local pins=/work/factory/snitchwatch-system-daemon-pins.json
    local patch=/work/factory/snitchwatch-system-daemon-shutdown.patch
    local stage=/work/factory/snitchwatch-system-daemon-stage.py
    local src=/work/src repository commit tree
    repository=$(pin sourceRepository)
    commit=$(pin sourceCommit)
    tree=$(pin sourceTree)
    unset GOFLAGS GOWORK GOOS GOARCH GOEXPERIMENT
    export GOENV=off GOWORK=off GOTOOLCHAIN=local CGO_ENABLED=1
    export GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
    export GOMODCACHE=/work/gomod GOBIN=/work/tools GOCACHE=/tmp/go-cache
    mkdir -p "$GOBIN"
    while IFS=$'\t' read -r module version package expected; do
        retry go mod download "$module@$version"
        go mod download -json "$module@$version" > /tmp/tool-download.json
        python3 - /tmp/tool-download.json "$module" "$version" "$expected" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
if (p.get('Path'), p.get('Version'), p.get('Sum')) != tuple(sys.argv[2:]):
    raise SystemExit('Go tool module checksum mismatch')
PY
        retry go install "$package@$version"
    done < <(python3 - "$pins" <<'PY'
import json, sys
for p in json.load(open(sys.argv[1]))['tools'].values():
    print('\t'.join(p[k] for k in ('module', 'version', 'package', 'sum')))
PY
)
    git init -q "$src"
    git -C "$src" remote add origin "$repository"
    retry git -C "$src" fetch -q --depth=1 origin "$commit"
    git -C "$src" checkout -q --detach FETCH_HEAD
    [[ "$(git -C "$src" rev-parse HEAD)" == "$commit" && "$(git -C "$src" rev-parse 'HEAD^{tree}')" == "$tree" ]] || {
        echo 'upstream commit/tree does not match the pins' >&2
        exit 1
    }
    python3 "$stage" check-source --pins "$pins" --source "$src" --patch "$patch"
    git -C "$src" apply --check "$patch"
    git -C "$src" apply "$patch"
    mkdir -p "$src/daemon/ui/protocol"
    (cd "$src/proto" && PATH="$GOBIN:$PATH" protoc -I. ui.proto --go_out=../daemon/ui/protocol/ \
        --go-grpc_out=../daemon/ui/protocol/ --go_opt=paths=source_relative --go-grpc_opt=paths=source_relative)
    python3 "$stage" check-source --pins "$pins" --source "$src" --patch "$patch" --patched
    (cd "$src/daemon" && retry go mod download)
    # go mod download may rewrite go.sum; prove go.mod/go.sum still match moduleLocks.
    python3 "$stage" check-source --pins "$pins" --source "$src" --patch "$patch" --patched
    # Patched Go files, relative to daemon/, for the gofmt check (upstream's
    # own files are not all gofmt-clean, so only the patch is held to it).
    python3 - "$pins" > /work/patched-go-files.txt <<'PY'
import json, sys
for name in sorted(json.load(open(sys.argv[1]))['patchedFiles']):
    if name.startswith('daemon/') and name.endswith('.go'):
        print(name.removeprefix('daemon/'))
PY
}

run_tests() {
    in_container test
    install_toolchain
    export GOENV=off GOWORK=off GOTOOLCHAIN=local CGO_ENABLED=1 GOFLAGS=-mod=readonly
    export GOPROXY=off GOSUMDB=off GOMODCACHE=/work/gomod GOCACHE=/tmp/go-cache
    # The confined blocklist reader refuses FUSE, and the tests fall back to
    # /dev/shm when /tmp is one; fail rather than skip if neither is usable.
    export SNITCHWATCH_REQUIRE_CONFINED_TESTS=1
    cd /work/src/daemon
    local -a patched_go
    readarray -t patched_go < /work/patched-go-files.txt
    # gofmt with no paths reads stdin and would pass vacuously.
    ((${#patched_go[@]})) || {
        echo 'no patched Go files listed in the pins' >&2
        exit 1
    }
    echo "== gofmt (${#patched_go[@]} patched Go files)"
    local unformatted
    unformatted=$(gofmt -l "${patched_go[@]}")
    [[ -z "$unformatted" ]] || {
        printf 'not gofmt-clean:\n%s\n' "$unformatted" >&2
        exit 1
    }
    echo "== go vet ./netfilter ./firewall/nftables ./statistics; . ./ui ./rule without ${UPSTREAM_VET_FINDINGS[*]}"
    go vet ./netfilter ./firewall/nftables ./statistics
    go vet "${UPSTREAM_VET_FINDINGS[@]}" . ./ui ./rule
    # Upstream's privileged nftables tests skip here (they need PRIVILEGED_TESTS
    # and namespace creation); the patch's own nftables tests run.
    echo "== go test -race (root, netfilter, firewall/nftables, statistics)"
    go test -count=1 -race . ./netfilter ./firewall/nftables ./statistics
    echo "== go test -race ./rule (skipping upstream timing flake: $UPSTREAM_RULE_RACE_FLAKES)"
    go test -count=1 -race -skip "$UPSTREAM_RULE_RACE_FLAKES" ./rule
    echo "== go test -race ./ui (skipping upstream race failures: $UPSTREAM_UI_RACE_FAILURES)"
    go test -count=1 -race -skip "$UPSTREAM_UI_RACE_FAILURES" ./ui
    echo "== go test (no -race; includes the skipped ui tests)"
    go test -count=1 . ./netfilter ./firewall/nftables ./statistics ./ui ./rule
    echo "PASS: OpenSnitch patch tests"
}

cleanup() {
    local rc=$?
    if ((${#names[@]})); then
        podman rm -f "${names[@]}" > /dev/null 2>&1 || true
    fi
    if [[ -n "$work" ]]; then
        # Go makes its module cache read-only; restore write access first.
        chmod -R u+w -- "$work" 2> /dev/null || true
        rm -rf -- "$work" || echo "warning: could not remove $work" >&2
    fi
    exit "$rc"
}

host() {
    local repo_root factory image
    repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
    factory="$repo_root/build_files/firewall"
    command -v podman > /dev/null || {
        echo 'podman is required' >&2
        exit 1
    }
    # The pinned Koji RPMs are installed by the toolchain helper; the factory's
    # live dnf extras plus those pinned names must cover TOOLCHAIN_RPMS.
    python3 - "$factory/snitchwatch-system-daemon-build.sh" "$factory/snitchwatch-system-daemon-pins.json" "${TOOLCHAIN_RPMS[@]}" <<'PY'
import json, re, sys
text = open(sys.argv[1]).read()
match = re.search(r'snitchwatch-system-toolchain\.sh" install [^\n]*\\\n([^\n]*)', text)
factory = set(match.group(1).split()) if match else set()
pinned = {r['name'] for r in json.load(open(sys.argv[2]))['toolchainRpms']}
if factory != set(sys.argv[3:]) - pinned:
    raise SystemExit('TOOLCHAIN_RPMS differs from the factory live dnf list: ' + ' '.join(sorted(factory)))
PY
    image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["builderImage"])' \
        "$factory/snitchwatch-system-daemon-pins.json")
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
        echo "builderImage must be pinned by digest: $image" >&2
        exit 1
    }
    trap cleanup EXIT
    work=$(mktemp -d "${TMPDIR:-/tmp}/snitchwatch-daemon-patch.XXXXXX")
    mkdir "$work/factory"
    for name in "${FACTORY_FILES[@]}"; do
        cp "$factory/$name" "$work/factory/$name"
    done
    cp "${BASH_SOURCE[0]}" "$work/factory/test-snitchwatch-daemon-patch.sh"
    names=("snitchwatch-daemon-patch-$$-prepare" "snitchwatch-daemon-patch-$$-test")
    # A private copy relabelled with :Z, so SELinux hosts never relabel the repo.
    # Without CAP_NET_ADMIN, the queue-permission test runs instead of skipping.
    local -a opts=(--rm --pull=missing --cap-drop=net_admin -v "$work:/work:Z")
    local -a cmd=("$image" bash /work/factory/test-snitchwatch-daemon-patch.sh)
    podman run "${opts[@]}" --name "${names[0]}" "${cmd[@]}" prepare
    podman run "${opts[@]}" --name "${names[1]}" --network=none "${cmd[@]}" test
}

case "${1:-}" in
    prepare) prepare ;;
    test) run_tests ;;
    '') host ;;
    *)
        echo "usage: $0" >&2
        exit 2
        ;;
esac
