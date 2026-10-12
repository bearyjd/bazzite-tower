#!/usr/bin/env python3
"""Read-only r13 publication gates. Trust only primary-reviewed control Git data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

REPOSITORY = 'bearyjd/bazzite-tower'
CANDIDATE_REPOSITORY = 'ghcr.io/bearyjd/bazzite-tower-r13-candidate'
STABLE_REPOSITORY = 'ghcr.io/bearyjd/bazzite-tower'
CANDIDATE_REF = 'refs/heads/ci/r13-candidate-hold-promotion'
CANDIDATE_WORKFLOW = '.github/workflows/r13-candidate.yml'
PROMOTION_WORKFLOW = '.github/workflows/r13-promote.yml'
FILES = {'image.oci.tar', 'candidate.json', 'sbom.json', 'build-provenance.json',
         'bridge-manifest.json', 'daemon-manifest.json', 'cosign.pub', 'policy.json', 'bazzite-tower.yaml'}
RECEIPTS = {'independent-head-review', 'independent-workflow-review', 'exact-head-ci',
            'candidate-policy', 'runtime-nfqueue', 'runtime-authorization', 'runtime-matrix',
            'runtime-reconnect', 'runtime-migration', 'runtime-rollback', 'writer-inventory'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def decode(data):
    require(len(data) <= 16 * 1024 * 1024, 'oversized JSON')
    return json.loads(data, object_pairs_hook=pairs)


def hex_value(value, size=64):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value), 'invalid hash')
    return value


def digest(value):
    require(isinstance(value, str) and value.startswith('sha256:'), 'invalid image digest')
    hex_value(value[7:])
    return value


def read(path, limit=16 * 1024 * 1024):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'invalid data file: ' + str(path))
    return path.read_bytes()


def file_sha(path):
    require(not path.is_symlink() and path.is_file(), 'invalid artifact file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashed_json(root, category, value):
    hex_value(value)
    data = read(root / '.github/r13' / category / (value + '.json'))
    require(sha(data) == value, 'trusted receipt/packet hash mismatch')
    result = decode(data)
    require(data == canonical(result), 'noncanonical trusted packet/receipt')
    return result


def receipt(root, value, kind, packet_hash, identities):
    r = hashed_json(root, 'receipts', value)
    require(set(r) == {'schemaVersion', 'kind', 'packetSha256', 'identities', 'reviewer', 'evidenceSha256', 'verdict'}, 'invalid receipt fields')
    require(r['schemaVersion'] == 1 and r['kind'] == kind and r['verdict'] == 'PASS', 'receipt refused')
    require(r['packetSha256'] == packet_hash and r['identities'] == identities, 'stale or substituted receipt')
    require(isinstance(r['reviewer'], str) and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}', r['reviewer']), 'missing trusted reviewer')
    hex_value(r['evidenceSha256'])
    return r


def authority(root):
    value = decode(read(root / '.github/r13/authority.json'))
    require(set(value) == {'schemaVersion', 'candidate', 'accepted'}, 'invalid authority fields')
    require(value['schemaVersion'] == 1, 'invalid authority version')
    return value


def identity(value):
    require(set(value) == {'towerCommit', 'towerTree', 'sourceCommit', 'sourceTree', 'submoduleCommit',
                           'bridgePinsSha256', 'daemonPinsSha256', 'workflowSha256'}, 'invalid source identity fields')
    for key in ('towerCommit', 'towerTree', 'sourceCommit', 'sourceTree', 'submoduleCommit'):
        hex_value(value[key], 40)
    for key in ('bridgePinsSha256', 'daemonPinsSha256', 'workflowSha256'):
        hex_value(value[key])


def candidate_approval(root, head):
    hex_value(head, 40)
    approved = authority(root)['candidate']
    require(isinstance(approved, dict) and set(approved) == {'identities', 'reviewReceiptSha256'}, 'candidate HOLD: no reviewed authority')
    ids = approved['identities']
    identity(ids)
    require(ids['towerCommit'] == head, 'unreviewed candidate head')
    receipt(root, approved['reviewReceiptSha256'], 'independent-workflow-review', sha(canonical(ids)), ids)
    return ids


def context(mode, head, ref, workflow, control_head):
    hex_value(control_head, 40)
    hex_value(head, 40)
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY, 'wrong repository')
    events = {'push', 'workflow_dispatch'} if mode == 'candidate' else {'workflow_dispatch'}
    require(os.environ.get('GITHUB_EVENT_NAME') in events, 'reviewed candidate push/manual promotion required')
    require(os.environ.get('GITHUB_SHA') == head, 'unreviewed workflow head')
    expected_ref = CANDIDATE_REF if mode == 'candidate' else 'refs/heads/main'
    expected_workflow = CANDIDATE_WORKFLOW if mode == 'candidate' else PROMOTION_WORKFLOW
    require(ref == expected_ref and workflow == REPOSITORY + '/' + expected_workflow + '@' + expected_ref, 'wrong workflow/ref')
    require(os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'rerun needs fresh review/identity')


def verify_source(root, ids):
    git = lambda *args: subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(git('rev-parse', 'HEAD') == ids['towerCommit'] and git('rev-parse', 'HEAD^{tree}') == ids['towerTree'], 'tower source mismatch')
    require(not git('status', '--porcelain', '--untracked-files=all'), 'dirty tower source')
    require(file_sha(root / CANDIDATE_WORKFLOW) == ids['workflowSha256'], 'workflow bytes changed')
    factory = root / 'build_files/firewall'
    pins = decode(read(factory / 'snitchwatch-system-pins.json'))
    for key in ('sourceCommit', 'sourceTree', 'submoduleCommit'):
        require(pins[key] == ids[key], 'source pin mismatch')
    for kind in ('bridge', 'daemon'):
        name = 'snitchwatch-system-' + ('daemon-' if kind == 'daemon' else '') + 'pins.json'
        require(file_sha(factory / name) == ids[kind + 'PinsSha256'], 'pins hash mismatch')


def image_identity(archive):
    """Inspect OCI headers/blobs as data; never unpack a rootfs or follow links."""
    with tarfile.open(archive, 'r:') as tar:
        members = {}
        for member in tar:
            name = member.name.removeprefix('./')
            if name in {'', '.'}:
                require(member.isdir(), 'unsafe OCI root entry')
                continue
            require(name not in members and not name.startswith('/') and '..' not in Path(name).parts, 'unsafe/duplicate OCI entry')
            require(member.isdir() or member.isfile(), 'OCI link/special entry')
            require(name in {'index.json', 'oci-layout', 'blobs', 'blobs/sha256'} or re.fullmatch('blobs/sha256/[0-9a-f]{64}', name), 'unexpected OCI entry')
            members[name] = member
            require(len(members) <= 10000, 'oversized OCI inventory')

        def blob(name, expected=None):
            require(name in members and members[name].isfile() and members[name].size <= 16 * 1024 * 1024, 'invalid OCI metadata')
            data = tar.extractfile(members[name]).read()
            if expected:
                require('sha256:' + sha(data) == expected, 'OCI metadata hash mismatch')
            return decode(data)

        require(blob('oci-layout') == {'imageLayoutVersion': '1.0.0'}, 'invalid OCI layout')
        index = blob('index.json')
        require(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1, 'one immutable image required')
        descriptor = index['manifests'][0]
        selected = digest(descriptor['digest'])
        require(members['blobs/sha256/' + selected[7:]].size == descriptor['size'], 'OCI descriptor size mismatch')
        manifest = blob('blobs/sha256/' + selected[7:], selected)
        # This one-machine image is deliberately single-platform. Refuse an
        # index instead of silently collapsing it during transport.
        require(manifest.get('mediaType') in {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}, 'multi-platform index HOLD')
        config_digest = digest(manifest['config']['digest'])
        config = blob('blobs/sha256/' + config_digest[7:], config_digest)
        require(config.get('architecture') == 'amd64' and config.get('os') == 'linux', 'wrong platform')
        for item in [manifest['config']] + manifest['layers']:
            value = digest(item['digest'])
            name = 'blobs/sha256/' + value[7:]
            require(name in members and members[name].isfile() and members[name].size == item['size'], 'missing OCI blob')
            stream = tar.extractfile(members[name])
            require('sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest() == value, 'OCI blob hash mismatch')
        return {'manifestDigest': selected, 'indexDigest': None, 'platformDigest': selected, 'platform': 'linux/amd64'}


def validate_candidate(root, data, head, run_id):
    ids = candidate_approval(root, head)
    require(set(p.name for p in data.iterdir()) == FILES, 'unexpected artifact inventory')
    for path in data.iterdir():
        limit = 40 * 1024 ** 3 if path.name == 'image.oci.tar' else 16 * 1024 ** 2
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'invalid/oversized artifact')
    candidate = decode(read(data / 'candidate.json'))
    require(set(candidate) == {'schemaVersion', 'repository', 'ref', 'workflow', 'runId', 'runAttempt', 'identities', 'image', 'files'}, 'invalid candidate metadata')
    require(candidate['schemaVersion'] == 1 and candidate['repository'] == CANDIDATE_REPOSITORY and candidate['ref'] == CANDIDATE_REF and candidate['workflow'] == CANDIDATE_WORKFLOW, 'candidate target/ref substitution')
    require(candidate['runId'] == run_id and candidate['runAttempt'] == 1 and candidate['identities'] == ids, 'candidate CI/source identity mismatch')
    require(candidate['image'] == image_identity(data / 'image.oci.tar'), 'candidate manifest/index/platform substitution')
    require(set(candidate['files']) == FILES - {'candidate.json'}, 'invalid candidate file hashes')
    for name, expected in candidate['files'].items():
        hex_value(expected)
        require(file_sha(data / name) == expected, 'artifact hash mismatch: ' + name)
    for name, source in [('cosign.pub', 'cosign.pub'), ('policy.json', 'system_files/usr/share/bazzite-tower/containers/policy.json'),
                         ('bazzite-tower.yaml', 'system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml')]:
        require(file_sha(data / name) == file_sha(root / source), 'trust policy/key substitution')
    bridge = decode(read(data / 'bridge-manifest.json'))
    daemon = decode(read(data / 'daemon-manifest.json'))
    require(bridge['profile'] == daemon['profile'] == 'system', 'wrong installed profile')
    require(bridge['source'] == {'commit': ids['sourceCommit'], 'tree': ids['sourceTree'], 'submoduleCommit': ids['submoduleCommit']}, 'bridge source mismatch')
    require(bridge['binary']['path'] == '/usr/bin/snitchwatch-bridge-cli' and daemon['binary']['path'] == '/usr/bin/opensnitchd', 'installed binary path substitution')
    require(daemon['source']['commit'] == ids['submoduleCommit'], 'daemon/submodule mismatch')
    hex_value(bridge['binary']['sha256']); hex_value(daemon['binary']['sha256'])
    sbom = decode(read(data / 'sbom.json'))
    require(sbom.get('spdxVersion') == 'SPDX-2.3' and sbom.get('packages'), 'invalid RPM inventory')
    provenance = decode(read(data / 'build-provenance.json'))
    require(provenance == {'schemaVersion': 1, 'kind': 'candidate-build', 'repository': REPOSITORY, 'workflow': CANDIDATE_WORKFLOW,
                           'ref': CANDIDATE_REF, 'runId': run_id, 'runAttempt': 1, 'identities': ids, 'image': candidate['image'],
                           'sbomSha256': candidate['files']['sbom.json'], 'bridgeManifestSha256': candidate['files']['bridge-manifest.json'],
                           'daemonManifestSha256': candidate['files']['daemon-manifest.json']}, 'build provenance substitution')
    return candidate


V2_PRODUCT = 'b2f6e24131de06117a00fb727e5cb7166d4e77da'
V2_TREE = '379895eef11de0539de415ed43e78d88ddab2b79'
V2_RECOVERY = 'fdfdfb0a6f80779d7e3ffc8d579bbea2c4c30b38'
V2_DIGEST = 'sha256:88f8ff0052c27fcc71379f2b43e06ddd7218419b2d90c8fcf8392af5292b8454'
V2_PATHS = {'.github/workflows/build.yml', '.github/workflows/boot-test.yml', '.github/workflows/r13-immutable-ci.yml'}
V2_KINDS = RECEIPTS | {'independent-product-equivalence', 'independent-policy-recovery'}
V2_JOBS = {'approval':114511635760, 'build':114511651930, 'validate':114516335933, 'publish':114517034908}


def v2_mapping(packet):
    b = packet['ciBinding']; r = packet['policyRecovery']; ids = packet['identities']
    require(set(b) == {'repository','pullRequest','baseRef','workflowHead','workflowTree','productHead','productTree','changedFiles','runs'}, 'invalid CI binding fields')
    require(b['repository'] == REPOSITORY and type(b['pullRequest']) is int and b['pullRequest'] == 107 and b['baseRef'] == 'main', 'wrong CI repository/PR/base')
    require(b['productHead'] == ids['towerCommit'] == V2_PRODUCT and b['productTree'] == ids['towerTree'] == V2_TREE, 'original product substituted')
    hex_value(b['workflowHead'],40); hex_value(b['workflowTree'],40)
    require(b['workflowHead'] != V2_PRODUCT, 'workflow head is not descendant identity')
    require(isinstance(b['changedFiles'],list) and len(b['changedFiles']) == 3, 'wrong workflow delta')
    require({x['path'] for x in b['changedFiles']} == V2_PATHS, 'unapproved changed paths')
    for x in b['changedFiles']:
        require(set(x) == {'path','originalBlob','reviewedBlob'}, 'invalid changed file')
        if x['path'].endswith('/r13-immutable-ci.yml'): require(x['originalBlob'] is None, 'new workflow existed')
        else: hex_value(x['originalBlob'],40)
        hex_value(x['reviewedBlob'],40)
    require(isinstance(b['runs'],list) and len(b['runs']) == 2, 'missing exact CI runs')
    require({x['workflow'] for x in b['runs']} == {'.github/workflows/build.yml','.github/workflows/boot-test.yml'}, 'wrong CI workflows')
    for x in b['runs']:
        require(set(x) == {'workflow','runId','attempt','jobIds'}, 'invalid CI run binding')
        require(type(x['runId']) is int and x['runId'] > 0 and type(x['attempt']) is int and x['attempt'] == 1, 'invalid CI run/attempt')
        require(isinstance(x['jobIds'],list) and x['jobIds'] and all(type(j) is int and j > 0 for j in x['jobIds']) and len(set(x['jobIds'])) == len(x['jobIds']), 'invalid CI jobs')
    require(len({x['runId'] for x in b['runs']}) == 2, 'duplicate CI run')
    require(set(r) == {'originalRunId','originalAttempt','originalJobs','recoveryHead','recoveryRunId','recoveryAttempt','recoveryJobId','digest','evidenceSha256'}, 'invalid recovery fields')
    expected={'originalRunId':38153505376,'originalAttempt':1,'originalJobs':V2_JOBS,'recoveryHead':V2_RECOVERY,'recoveryRunId':38168060273,'recoveryAttempt':1,'recoveryJobId':114554662264,'digest':V2_DIGEST}
    require(canonical({k:r[k] for k in expected}) == canonical(expected), 'recovery substitution')
    hex_value(r['evidenceSha256'])
    c=packet['candidate']
    require(c['runId']==38153505376 and c['artifactId']==11708196868 and c['artifactSha256']=='4e323596b62ce9d521c5a4f5f9c90fc9406cfc7ea6d065fa3516e9723776dec8' and c['image']['manifestDigest']==V2_DIGEST, 'original artifact/image substitution')


def receipt_v2(root, value, kind, packet_hash, packet):
    r=hashed_json(root,'receipts',value)
    require(set(r)=={'schemaVersion','kind','packetSha256','identities','reviewer','evidenceSha256','verdict','ciBindingSha256','policyRecoverySha256'}, 'invalid v2 receipt fields')
    require(r['schemaVersion']==2 and r['kind']==kind and r['verdict']=='PASS', 'v2 receipt refused')
    require(r['packetSha256']==packet_hash and r['identities']==packet['identities'], 'v2 receipt packet/product mismatch')
    require(r['ciBindingSha256']==sha(canonical(packet['ciBinding'])) and r['policyRecoverySha256']==sha(canonical(packet['policyRecovery'])), 'v2 receipt mapping mismatch')
    require(isinstance(r['reviewer'],str) and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}',r['reviewer']), 'missing v2 reviewer')
    hex_value(r['evidenceSha256'])
    if kind=='independent-policy-recovery': require(r['evidenceSha256']==packet['policyRecovery']['evidenceSha256'], 'recovery evidence mismatch')


def v2_git_binding(repo, binding):
    def git(*args): return subprocess.check_output(['git','-C',str(repo),*args],timeout=90).decode().strip()
    h=binding['workflowHead']
    require(git('rev-parse',h+'^{tree}')==binding['workflowTree'] and git('rev-parse',V2_PRODUCT+'^{tree}')==V2_TREE, 'Git tree mismatch')
    subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',V2_PRODUCT,h],check=True,timeout=90)
    changed=set(filter(None,git('diff','--no-renames','--name-only','-z',V2_PRODUCT,h).split('\0')))
    require(changed==V2_PATHS, 'non-workflow Git delta')
    for x in binding['changedFiles']:
        for commit,key in ((V2_PRODUCT,'originalBlob'),(h,'reviewedBlob')):
            entry=git('ls-tree',commit,'--',x['path'])
            if x[key] is None: require(not entry,'unexpected original entry')
            else: require(entry==f"100644 blob {x[key]}\t{x['path']}", 'blob or Git mode mismatch')


def verify_v2_live(packet):
    # Fresh observations only; no status writes, signing or authority mutation.
    import importlib.util
    import tempfile
    v2_mapping(packet)
    def api(endpoint):
        raw=subprocess.check_output(['gh','api','repos/'+REPOSITORY+'/'+endpoint],timeout=90)
        require(len(raw)<=16*1024**2,'oversized GitHub response')
        return decode(raw)
    b=packet['ciBinding']
    pr=api('pulls/107')
    require(pr['head']['sha']==b['workflowHead'] and pr['head']['repo']['full_name']==REPOSITORY and pr['base']['repo']['full_name']==REPOSITORY and pr['base']['ref']=='main','PR head/base changed')
    for bound in b['runs']:
        run=api(f"actions/runs/{bound['runId']}")
        require(run['id']==bound['runId'] and run['head_sha']==b['workflowHead'] and run['path']==bound['workflow'] and run['run_attempt']==1 and run['status']=='completed' and run['conclusion']=='success' and run['event']=='pull_request','exact H CI failed/stale')
        require(run['repository']['full_name']==run['head_repository']['full_name']==REPOSITORY and any(x['number']==107 for x in run['pull_requests']),'foreign CI')
        jobs=api(f"actions/runs/{bound['runId']}/jobs?per_page=100")
        require(jobs['total_count']==len(jobs['jobs']) and {x['id'] for x in jobs['jobs']}==set(bound['jobIds']),'CI job inventory mismatch')
        required={'Verify image (safe-pin)','Verify image (latest-kernel)','Verify image (snitchwatch-system)'} if bound['workflow'].endswith('/build.yml') else set()
        required.add('Verify original r13 immutable candidate / verify-original')
        for name in required:
            found=[j for j in jobs['jobs'] if j['name']==name]
            require(len(found)==1,'required CI job absent/ambiguous')
            j=found[0]
            require(j['run_id']==bound['runId'] and j['head_sha']==b['workflowHead'] and j['run_attempt']==1 and j['status']=='completed' and j['conclusion']=='success','required dependency did not succeed')
            url=j['check_run_url']; prefix='https://api.github.com/repos/'+REPOSITORY+'/check-runs/'
            require(url.startswith(prefix) and url[len(prefix):].isdigit(),'foreign check URL')
            check=api('check-runs/'+url[len(prefix):])
            require(check['name']==name and check['head_sha']==b['workflowHead'] and check['app']['id']==15368 and check['status']=='completed' and check['conclusion']=='success','check ownership/result mismatch')
    with tempfile.TemporaryDirectory(prefix='r13-v2-') as temp:
        repo=Path(temp)/'objects'
        subprocess.run(['git','init','--bare',str(repo)],check=True,stdout=subprocess.DEVNULL,timeout=30)
        subprocess.run(['git','-C',str(repo),'fetch','--no-tags','https://github.com/'+REPOSITORY+'.git',V2_PRODUCT,b['workflowHead'],V2_RECOVERY],check=True,stdout=subprocess.DEVNULL,timeout=180)
        v2_git_binding(repo,b)
        code=subprocess.check_output(['git','-C',str(repo),'show',V2_RECOVERY+':ci/r13-candidate-policy-recovery.py'],timeout=30)
        helper=Path(temp)/'recovery.py'; helper.write_bytes(code)
        spec=importlib.util.spec_from_file_location('fixed_recovery',helper); r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
        original=api('actions/runs/38153505376'); jobs=api('actions/runs/38153505376/jobs?per_page=100'); artifact=api('actions/artifacts/11708196868')
        r.original_metadata(original,jobs,artifact)
        recovered=api('actions/runs/38168060273')
        run_receipt(recovered,repository=REPOSITORY,head=V2_RECOVERY,workflow=r.RECOVERY_WORKFLOW,ref=r.RECOVERY_REF,run_id=38168060273)
        j=api('actions/jobs/114554662264')
        require(j['id']==114554662264 and j['run_id']==38168060273 and j['head_sha']==V2_RECOVERY and j['run_attempt']==1 and j['name']=='recover-policy' and j['status']=='completed' and j['conclusion']=='success','fixed recovery job mismatch')
    # Refuse a head move during observations too.
    require(api('pulls/107')['head']['sha']==b['workflowHead'],'PR head moved during validation')


def accepted(root, packet_hash):
    hex_value(packet_hash)
    approved = authority(root)['accepted']
    require(isinstance(approved, dict) and set(approved) == {'packetSha256', 'receipts'}, 'stable HOLD: no accepted packet')
    require(approved['packetSha256'] == packet_hash, 'packet hash not primary accepted')
    packet = hashed_json(root, 'packets', packet_hash)
    require(type(packet['schemaVersion']) is int and packet['schemaVersion'] in (1,2), 'unsupported acceptance packet')
    v2 = packet['schemaVersion'] == 2
    require(set(packet) == {'schemaVersion', 'identities', 'candidate', 'publication', 'gui'} | ({'ciBinding','policyRecovery'} if v2 else set()), 'invalid acceptance packet')
    if v2: v2_mapping(packet)
    ids = packet['identities']; identity(ids)
    require(ids == candidate_approval(root, ids['towerCommit']), 'candidate review changed')
    require(set(approved['receipts']) == (V2_KINDS if v2 else RECEIPTS), 'missing trusted independent/runtime receipts')
    for kind, value in approved['receipts'].items():
        if v2: receipt_v2(root, value, kind, packet_hash, packet)
        else: receipt(root, value, kind, packet_hash, ids)
    c = packet['candidate']
    require(set(c) == {'runId', 'artifactId', 'artifactSha256', 'metadataSha256', 'image', 'ref', 'workflow'}, 'invalid candidate packet')
    require(c['ref'] == CANDIDATE_REF and c['workflow'] == CANDIDATE_WORKFLOW, 'candidate ref/workflow mismatch')
    for key in ('runId', 'artifactId'):
        require(type(c[key]) is int and c[key] > 0, 'invalid run/artifact ID')
    for key in ('artifactSha256', 'metadataSha256'):
        hex_value(c[key])
    require(set(c['image']) == {'manifestDigest', 'indexDigest', 'platformDigest', 'platform'}, 'invalid accepted image')
    digest(c['image']['manifestDigest'])
    require(c['image']['indexDigest'] is None and c['image']['platformDigest'] == c['image']['manifestDigest'] and c['image']['platform'] == 'linux/amd64', 'accepted platform/index substitution')
    require(set(packet['publication']) == {'runId', 'workflowCommit', 'workflow', 'ref'}, 'invalid publication receipt')
    require(packet['publication'] == {'runId': c['runId'], 'workflowCommit': ids['towerCommit'], 'workflow': CANDIDATE_WORKFLOW, 'ref': CANDIDATE_REF}, 'publication identity mismatch')
    gui = packet['gui']
    require(set(gui) == {'repository', 'sourceCommit', 'sourceTree', 'submoduleCommit', 'profile', 'runtimeRef', 'manifestSha256', 'bundleSha256', 'runId', 'workflow', 'ref', 'artifactId', 'artifactSha256'}, 'invalid GUI identity')
    require(gui['repository'] == 'bearyjd/snitchwatch' and gui['profile'] == 'system' and gui['sourceCommit'] == ids['sourceCommit'] and gui['sourceTree'] == ids['sourceTree'] and gui['submoduleCommit'] == ids['submoduleCommit'], 'GUI/source/profile mismatch')
    require(gui['runtimeRef'] == 'org.kde.Platform/x86_64/6.11', 'wrong GUI runtime')
    for key in ('manifestSha256', 'bundleSha256', 'artifactSha256'):
        hex_value(gui[key])
    for key in ('runId', 'artifactId'):
        require(type(gui[key]) is int and gui[key] > 0, 'invalid GUI run/artifact')
    require(isinstance(gui['workflow'], str) and re.fullmatch(r'\.github/workflows/[a-z0-9-]+\.yml', gui['workflow']), 'invalid GUI workflow')
    require(isinstance(gui['ref'], str) and re.fullmatch(r'refs/heads/[a-zA-Z0-9_/-]+', gui['ref']), 'invalid GUI ref')
    return packet


def run_receipt(run, *, repository, head, workflow, ref, run_id):
    require(run['id'] == run_id and run['head_sha'] == head and run['head_branch'] == ref.removeprefix('refs/heads/') and run['path'] == workflow, 'stale CI identity')
    require(run['repository']['full_name'] == run['head_repository']['full_name'] == repository and run['event'] in {'push', 'workflow_dispatch'}, 'foreign CI/ref')
    require(run['status'] == 'completed' and run['conclusion'] == 'success' and run['run_attempt'] == 1, 'CI not exact green first attempt')


def artifact_receipt(value, expected, run_id, name):
    require(value['id'] == expected['artifactId'] and value['name'] == name and not value['expired'], 'missing/substituted artifact')
    require(value['digest'] == 'sha256:' + expected['artifactSha256'] and value['workflow_run']['id'] == run_id, 'artifact/run hash substitution')


def output(values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in values.items():
            require(isinstance(key, str) and re.fullmatch('[a-z_][a-z0-9_]*', key), 'invalid output name')
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))
            require('\n' not in text and '\r' not in text, 'output injection')
            stream.write(key + '=' + text + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['approval', 'source', 'candidate', 'promote'])
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--packet-sha256')
    args = parser.parse_args()
    mode = 'promote' if args.mode == 'promote' else 'candidate'
    head = os.environ.get('R13_PROMOTION_HEAD' if mode == 'promote' else 'R13_CANDIDATE_HEAD', '')
    context(mode, head, os.environ.get('GITHUB_REF', ''), os.environ.get('GITHUB_WORKFLOW_REF', ''), os.environ.get('R13_CONTROL_HEAD', ''))
    if args.mode == 'approval':
        candidate_approval(args.control, head)
    elif args.mode == 'source':
        verify_source(args.source, candidate_approval(args.control, head))
    elif args.mode == 'candidate':
        c = validate_candidate(args.control, args.data, head, int(os.environ['GITHUB_RUN_ID']))
        output({'digest': c['image']['manifestDigest'], 'hashes': {p.name: file_sha(p) for p in args.data.iterdir()},
                'head': head, 'run_id': os.environ['GITHUB_RUN_ID']})
    else:
        p = accepted(args.control, args.packet_sha256)
        c = p['candidate']; g = p['gui']; ids = p['identities']
        if p['schemaVersion'] == 2: verify_v2_live(p)
        else:
            run_receipt(decode(read(args.data / 'tower-run.json')), repository=REPOSITORY, head=ids['towerCommit'], workflow=c['workflow'], ref=c['ref'], run_id=c['runId'])
        run_receipt(decode(read(args.data / 'gui-run.json')), repository=g['repository'], head=g['sourceCommit'], workflow=g['workflow'], ref=g['ref'], run_id=g['runId'])
        artifact_receipt(decode(read(args.data / 'tower-artifact.json')), c, c['runId'], 'r13-image-data')
        artifact_receipt(decode(read(args.data / 'gui-artifact.json')), g, g['runId'], 'r13-system-gui-candidate')
        data = args.data / 'tower'
        require(file_sha(data / 'candidate.json') == c['metadataSha256'], 'accepted metadata hash changed')
        value = validate_candidate(args.control, data, ids['towerCommit'], c['runId'])
        require(value['image'] == c['image'], 'accepted image changed')
        bridge = decode(read(data / 'bridge-manifest.json'))
        require(bridge['flatpak']['manifestSha256'] == g['manifestSha256'] and bridge['flatpak']['runtimeRef'] == g['runtimeRef'], 'image/GUI manifest mismatch')
        require(file_sha(args.data / 'gui' / 'snitchwatch-system-r13-candidate.flatpak') == g['bundleSha256'], 'accepted GUI bytes changed')
        output({'digest': c['image']['manifestDigest'], 'hashes': {p.name: file_sha(p) for p in data.iterdir()},
                'candidate_head': ids['towerCommit'], 'candidate_run_id': str(c['runId']), 'artifact_id': str(c['artifactId']),
                'gui_run_id': str(g['runId']), 'gui_artifact_id': str(g['artifactId']), 'gui_sha256': g['bundleSha256'],
                'gui_identity': g,
                'packet_sha256': args.packet_sha256})


if __name__ == '__main__':
    main()
