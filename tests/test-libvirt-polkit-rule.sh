#!/usr/bin/env bash
# Offline behavioural check of the libvirt polkit rule shipped in system_files/.
# Emulates polkit's rule engine with node (no polkit, no network): rule files are
# evaluated in lexical filename order and the first non-undefined result wins.
# A stand-in for libvirt's upstream 50-libvirt.rules (group libvirt -> manage =
# YES) is loaded alongside ours to prove ours preempts it. This validates the
# rule's JS syntax and logic only, NOT real polkitd behaviour (that needs the
# laptop; see docs/RUNBOOK.md "Docker without silent root").
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
rule="${repo_root}/system_files/usr/share/polkit-1/rules.d/49-bazzite-tower-libvirt-manage.rules"

if ! command -v node >/dev/null 2>&1; then
    echo "node not found: libvirt polkit rule check SKIPPED (unverified)" >&2
    exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
install -d "${tmp}/rules.d"
install -m 0644 -- "${rule}" "${tmp}/rules.d/49-bazzite-tower-libvirt-manage.rules"
install -m 0644 /dev/stdin "${tmp}/rules.d/50-libvirt.rules" <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "org.libvirt.unix.manage" && subject.isInGroup("libvirt")) {
        return polkit.Result.YES;
    }
});
EOF

# Syntax check (rules are plain JS; node only recognises .js, so feed stdin).
node --check - < "${tmp}/rules.d/49-bazzite-tower-libvirt-manage.rules"

node - "${tmp}/rules.d" <<'EOF'
const fs = require('fs');
const path = require('path');
const dir = process.argv[2];
const rules = [];
const polkit = {
    Result: { YES: 'yes', NO: 'no', AUTH_ADMIN_KEEP: 'auth_admin_keep', NOT_HANDLED: undefined },
    addRule: (fn) => rules.push(fn),
};
for (const f of fs.readdirSync(dir).sort()) {   // C-locale lexical order
    new Function('polkit', fs.readFileSync(path.join(dir, f), 'utf8'))(polkit);
}
const decide = (id, groups, extra) => {
    const subject = Object.assign({ local: true, active: true, isInGroup: (g) => groups.includes(g) }, extra);
    for (const r of rules) {
        const v = r({ id }, subject);
        if (v !== undefined) return v;
    }
    return undefined;
};
const M = 'org.libvirt.unix.manage', R = 'org.libvirt.unix.monitor';
const cases = [
    ['manage, wheel only',                 M, ['wheel'],            {}, 'auth_admin_keep'],
    ['manage, libvirt only (upstream YES preempted)', M, ['libvirt'], {}, 'auth_admin_keep'],
    ['manage, wheel+libvirt',              M, ['wheel', 'libvirt'], {}, 'auth_admin_keep'],
    ['manage, libvirt, not local/active',  M, ['libvirt'], { local: false, active: false }, 'auth_admin_keep'],
    ['manage, no relevant group',          M, ['users'],            {}, undefined],
    ['monitor, wheel (action default applies)',   R, ['wheel'],     {}, undefined],
    ['monitor, libvirt (action default applies)', R, ['libvirt'],   {}, undefined],
];
let failed = 0;
for (const [name, id, groups, extra, want] of cases) {
    const got = decide(id, groups, extra);
    const ok = got === want;
    if (!ok) failed = 1;
    console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name}: ${got}`);
}
process.exit(failed);
EOF
echo "libvirt polkit rule logic (emulated engine): pass"
