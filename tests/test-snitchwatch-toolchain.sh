#!/usr/bin/env bash
# Offline contract for build_files/firewall/snitchwatch-system-toolchain.sh:
# curl/rpm/dnf are PATH stubs, so each refusal case passes only because the
# helper itself rejects the input (checked by message, and dnf never ran).
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
helper="${HELPER:-${repo_root}/build_files/firewall/snitchwatch-system-toolchain.sh}"
real_pins="${repo_root}/build_files/firewall/snitchwatch-system-daemon-pins.json"
T=$(mktemp -d)
trap 'rm -rf -- "$T"' EXIT
fails=0
bad()  { printf 'FAIL: %s\n' "$*" >&2; fails=$((fails + 1)); }
good() { printf 'ok: %s\n' "$*"; }

# --- static: the real pins list exactly the reviewed toolchain, with sane hashes
python3 - "$real_pins" <<'PY' || bad 'real pins: expected 7 RPMs with 64-hex sha256, https kojiBase, pinned key'
import json,re,sys
p=json.load(open(sys.argv[1]))
names=sorted(r['name'] for r in p['toolchainRpms'])
want=sorted(['golang','golang-bin','golang-src','protobuf','protobuf-compiler','libnetfilter_queue','libnetfilter_queue-devel'])
assert names==want,names
assert all(re.fullmatch(r'[0-9a-f]{64}',r['sha256']) for r in p['toolchainRpms'])
assert re.fullmatch(r'[0-9a-f]{64}',p['rpmSigningKey']['sha256'])
assert p['kojiBase'].startswith('https://') and all('/signed/'+p['signedKeyId']+'/' in r['kojiPath'] for r in p['toolchainRpms'])
PY
good 'real pins list the 7 reviewed RPMs'

# --- fixtures: two fake RPMs, a fake key, stub commands
mkdir -p "$T/bin" "$T/served" "$T/installed"
printf 'fake key\n' > "$T/key.asc"
printf 'abcdef0123456789abcdef0123456789abcdef01' > "$T/fpr"
printf 'rpm-one-payload\n' > "$T/served/one-1.0-1.fc44.x86_64.rpm"
printf 'rpm-two-payload-longer\n' > "$T/served/two-2.0-1.fc44.noarch.rpm"
printf 'one-0:1.0-1.fc44.x86_64' > "$T/served/one-1.0-1.fc44.x86_64.rpm.nevra"
printf 'two-0:2.0-1.fc44.noarch' > "$T/served/two-2.0-1.fc44.noarch.rpm.nevra"

cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
out='' url=''
while (($#)); do
    case $1 in --output) out=$2; shift ;; --*) ;; *) url=$1 ;; esac
    shift
done
echo "$url" >> "$STUB_DIR/curl.log"
src="$STUB_DIR/served/${url##*/}"
[[ -n "${STUB_EMPTY:-}" ]] && : > "$out" && exit 0
[[ -f "$src" ]] || exit 22
cp -- "$src" "$out"
STUB
cat > "$T/bin/rpm" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *--import*) exit 0 ;;
    *-qa*) if [[ -n "${STUB_OTHERKEY:-}" ]]; then echo 1111111111111111111111111111111111111111; else cat "$STUB_DIR/fpr"; fi ;;
    *-qp*SHA256HEADER*) echo "hdr-$(basename "${!#}")" ;;
    *SHA256HEADER*) echo "hdr-$(cat "$STUB_DIR/installed/${!#}.file")" ;;
    *" -K "*) if [[ -n "${STUB_UNSIGNED:-}" ]]; then echo "${!#}: digests OK"; else echo "${!#}: digests signatures OK"; fi ;;
    *-qp*) if [[ -n "${STUB_BADNEVRA:-}" ]]; then echo wrong-0:9-9.fc44.x86_64; else cat "$STUB_DIR/served/$(basename "${!#}").nevra"; fi ;;
    *-q*) cat "$STUB_DIR/installed/${!#}" ;;
esac
STUB
cat > "$T/bin/dnf" <<'STUB'
#!/usr/bin/env bash
echo ran >> "$STUB_DIR/dnf.log"
[[ -z "${STUB_DNFFAIL:-}" ]] || exit 1
for a in "$@"; do
    if [[ $a == *.rpm ]]; then
        n=$(basename "$a" | sed 's/-[0-9].*//')
        cp -- "$STUB_DIR/served/$(basename "$a").nevra" "$STUB_DIR/installed/$n"
        if [[ -n "${STUB_BADORIGIN:-}" ]]; then echo other.rpm > "$STUB_DIR/installed/$n.file"; else basename "$a" > "$STUB_DIR/installed/$n.file"; fi
    fi
done
exit 0
STUB
chmod +x "$T/bin/"*

# mkpins <mutation> -> pins file in $T/pins.json (mutation applied after a valid build)
mkpins() {
    python3 - "$T" "$1" <<'PY'
import hashlib,json,os,sys
T,mut=sys.argv[1:3]
sha=lambda p:hashlib.sha256(open(p,'rb').read()).hexdigest()
rpms=[]
for n,f,d in (('one','one-1.0-1.fc44.x86_64.rpm','x86_64'),('two','two-2.0-1.fc44.noarch.rpm','noarch')):
    p=f'{T}/served/{f}'
    rpms.append({'name':n,'file':f,'kojiPath':f'{n}/1/1/data/signed/abc/{d}/{f}','sha256':sha(p),'size':os.path.getsize(p),
                 'nevra':open(p+'.nevra').read()})
pins={'kojiBase':'https://koji.test/packages/','rpmSigningKey':{'path':f'{T}/key.asc','sha256':sha(f'{T}/key.asc'),'fingerprint':'ABCDEF0123456789ABCDEF0123456789ABCDEF01'},'toolchainRpms':rpms}
if mut=='badkeyfmt':pins['rpmSigningKey']['sha256']='xyz'
if mut=='badfprfmt':pins['rpmSigningKey']['fingerprint']='not-a-fingerprint'
if mut=='badsha':pins['toolchainRpms'][1]['sha256']='0'*64
if mut=='badkey':pins['rpmSigningKey']['sha256']='0'*64
if mut=='http':pins['kojiBase']='http://koji.test/packages/'
if mut=='dotdot':pins['toolchainRpms'][0]['kojiPath']='one/../../etc/x.rpm'
if mut=='abs':pins['toolchainRpms'][0]['kojiPath']='/'+pins['toolchainRpms'][0]['kojiPath']
if mut=='badsize':pins['toolchainRpms'][0]['size']+=1
json.dump(pins,open(f'{T}/pins.json','w'))
PY
}

# run_case <name> <expect: ok|refuse|dnffail> <mutation> <stderr-fragment> [ENV=1 ...]
run_case() {
    local name=$1 expect=$2 mut=$3 frag=$4 rc
    shift 4
    rm -f -- "$T/curl.log" "$T/dnf.log" "$T/err.txt"
    rm -f -- "$T/installed/"* 2> /dev/null || true
    mkpins "$mut"
    rm -rf -- "$T/work" 2> /dev/null || true
    local -a extras=(extra-pkg)
    [[ "${MODE:-install}" == install ]] || extras=()
    env PATH="$T/bin:$PATH" STUB_DIR="$T" "$@" bash "$helper" "${MODE:-install}" "$T/pins.json" "$T/work" "${extras[@]}" > /dev/null 2> "$T/err.txt"
    rc=$?
    case $expect in
        ok)
            if [[ $rc -eq 0 && -f "$T/dnf.log" ]]; then good "$name"; else bad "$name (rc=$rc) $(cat "$T/err.txt")"; fi ;;
        refuse)
            if [[ $rc -ne 0 && ! -f "$T/dnf.log" ]] && grep -q -- "$frag" "$T/err.txt"; then good "$name"; else bad "$name (rc=$rc, dnf ran: $([[ -f $T/dnf.log ]] && echo yes || echo no)) $(cat "$T/err.txt")"; fi ;;
        dnffail)
            if [[ $rc -ne 0 && -f "$T/dnf.log" ]] && grep -q -- "$frag" "$T/err.txt"; then good "$name"; else bad "$name (rc=$rc) $(cat "$T/err.txt")"; fi ;;
    esac
}

run_case 'happy path installs and confirms pinned versions' ok none ''
run_case 'wrong rpm sha256 is refused' refuse badsha 'sha256 mismatch'
run_case 'wrong size is refused' refuse badsize 'size mismatch'
run_case 'unsigned rpm (digests OK only) is refused' refuse none 'unsigned or not signed' STUB_UNSIGNED=1
run_case 'wrong signing-key sha256 is refused' refuse badkey 'signing key does not match'
run_case 'non-https kojiBase is refused' refuse http 'kojiBase must be an https URL'
run_case 'kojiPath with .. is refused' refuse dotdot 'unsafe kojiPath'
run_case 'kojiPath with leading / is refused' refuse abs 'unsafe kojiPath'
run_case 'empty download is refused' refuse none 'empty download' STUB_EMPTY=1
run_case 'NEVRA mismatch is refused' refuse none 'NEVRA mismatch' STUB_BADNEVRA=1
run_case 'dnf failure exits non-zero' dnffail none 'dnf install failed' STUB_DNFFAIL=1
run_case 'malformed key sha256 pin is refused' refuse badkeyfmt 'malformed signing-key sha256'
run_case 'malformed key fingerprint pin is refused' refuse badfprfmt 'malformed signing-key fingerprint'
run_case 'imported key not matching the pinned fingerprint is refused' refuse none 'does not match the pinned fingerprint' STUB_OTHERKEY=1
run_case 'installed package from another origin (header differs) is refused' dnffail none 'not the verified file' STUB_BADORIGIN=1

# fetch mode: verifies and leaves RPMs in <workdir>/rpms, never runs dnf
fetch_case() {
    local name=$1 expect=$2 mut=$3 frag=$4
    shift 4
    MODE=fetch run_case "fetch: $name" "$expect" "$mut" "$frag" "$@"
}
fetch_case 'wrong rpm sha256 is refused' refuse badsha 'sha256 mismatch'
fetch_case 'unsigned rpm is refused' refuse none 'unsigned or not signed' STUB_UNSIGNED=1
fetch_case 'NEVRA mismatch is refused' refuse none 'NEVRA mismatch' STUB_BADNEVRA=1
fetch_case 'wrong signing-key sha256 is refused' refuse badkey 'signing key does not match'
rm -f -- "$T/curl.log" "$T/dnf.log"; mkpins none; rm -rf -- "$T/work" 2> /dev/null || true
if env PATH="$T/bin:$PATH" STUB_DIR="$T" bash "$helper" fetch "$T/pins.json" "$T/work" > /dev/null 2> "$T/err.txt" \
    && [[ ! -f "$T/dnf.log" && -s "$T/work/rpms/one-1.0-1.fc44.x86_64.rpm" && -s "$T/work/rpms/two-2.0-1.fc44.noarch.rpm" ]]; then
    good 'fetch: verified RPMs left in workdir/rpms, dnf never ran'
else bad "fetch happy path: $(cat "$T/err.txt")"; fi
if env PATH="$T/bin:$PATH" STUB_DIR="$T" bash "$helper" fetch "$T/pins.json" "$T/work" extra-pkg > /dev/null 2> "$T/err.txt"; then
    bad 'fetch with extra packages should be refused'
elif grep -q 'fetch takes no extra packages' "$T/err.txt"; then good 'fetch: extra packages refused'
else bad "fetch extras msg: $(cat "$T/err.txt")"; fi

((fails == 0)) || { echo "snitchwatch-toolchain: $fails failure(s)" >&2; exit 1; }
echo 'snitchwatch-toolchain: pass'
