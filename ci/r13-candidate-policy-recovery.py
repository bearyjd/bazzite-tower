#!/usr/bin/env python3
"""One immutable failed-run policy recovery; original build provenance stays b2."""
import argparse
import base64
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import sys
import time
import zipfile

sys.dont_write_bytecode = True

REPO = 'bearyjd/bazzite-tower'
HEAD = 'b2f6e24131de06117a00fb727e5cb7166d4e77da'
CONTROL = '70825044d78c133da0dad61c9ac651b561c747b6'
CONTROL_TREE = '588364d73c605d15b9cbc06cbca04b8038d00f56'
REF = 'refs/heads/ci/r13-candidate-hold-promotion'
WORKFLOW = '.github/workflows/r13-candidate.yml'
RECOVERY_REF = 'refs/heads/ci/r13-candidate-policy-recovery'
RECOVERY_WORKFLOW = '.github/workflows/r13-candidate-policy-recovery.yml'
RUN = 38153505376
ARTIFACT = 11708196868
ZIP_SHA = '4e323596b62ce9d521c5a4f5f9c90fc9406cfc7ea6d065fa3516e9723776dec8'
ZIP_SIZE = 14589870664
DIGEST = 'sha256:88f8ff0052c27fcc71379f2b43e06ddd7218419b2d90c8fcf8392af5292b8454'
DESTINATION = 'ghcr.io/bearyjd/bazzite-tower-r13-candidate'
VALIDATOR_SHA = '720a574733853063dd96e7ee46f5f6d8da4fd2c618fbd732153c2b79821b457c'
JOBS = {'approval':114511635760,'build':114511651930,'validate':114516335933,'publish':114517034908}
LIMITS = {'image.oci.tar':40*1024**3, **{name:16*1024**2 for name in ('candidate.json','sbom.json','build-provenance.json','bridge-manifest.json','daemon-manifest.json','cosign.pub','policy.json','bazzite-tower.yaml')}}
PINNED_FILES = {'image.oci.tar':'5e9103b2ed3703bf25168f98d0d35c9ba1bc4d72c7a2b8b24e583a40c4127e5f','build-provenance.json':'b12547c89fa9be5addea68c4a4158243da86f5c349944d105806a6bd507d75cd','sbom.json':'ee962700d3396d85609a83a5680a974cac996ec87e59fb2d0fc6170adc8dd128'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value

def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs)

def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def save(path, value):
    with path.open('xb') as stream:
        stream.write((json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode())

def context(code):
    expected = os.environ.get('R13_RECOVERY_HEAD','')
    require(re.fullmatch('[0-9a-f]{40}',expected) is not None, 'recovery head not approved')
    require(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_EVENT_NAME')=='push', 'wrong recovery repository/event')
    require(os.environ.get('GITHUB_REF')==RECOVERY_REF and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/'+RECOVERY_WORKFLOW+'@'+RECOVERY_REF, 'wrong recovery workflow/ref')
    require(os.environ.get('GITHUB_SHA')==expected and os.environ.get('GITHUB_RUN_ATTEMPT')=='1', 'unreviewed head or rerun')
    require(re.fullmatch('[1-9][0-9]*',os.environ.get('GITHUB_RUN_ID','')) is not None and int(os.environ['GITHUB_RUN_ID'])!=RUN, 'recovery run identity missing')
    require(subprocess.check_output(['git','-C',str(code),'rev-parse','HEAD'],text=True,timeout=20).strip()==expected, 'actual recovery checkout differs')
    require(not subprocess.check_output(['git','-C',str(code),'status','--porcelain','--untracked-files=all'],timeout=20), 'recovery code checkout dirty')

def validator(root):
    require(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,timeout=20).strip()==CONTROL, 'actual original control checkout differs')
    require(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD^{tree}'],text=True,timeout=20).strip()==CONTROL_TREE, 'original control tree differs')
    require(not subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=all'],timeout=20), 'original control checkout dirty')
    path=root/'ci/r13-publication.py'
    require(file_sha(path)==VALIDATOR_SHA, 'original validator changed')
    spec=importlib.util.spec_from_file_location('original_publication',path)
    pub=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)
    ids=pub.candidate_approval(root,HEAD)
    require(pub.authority(root)['accepted'] is None, 'stable authority is not HOLD')
    require(ids['towerCommit']==HEAD and ids['towerTree']=='379895eef11de0539de415ed43e78d88ddab2b79' and ids['sourceCommit']=='1527fd4df9070704ae9b98f8c3011461919812e4' and ids['sourceTree']=='cc9fa429825b18a592fe4e721d51a0dec42b2476', 'original frozen product/source differs')
    return pub

def capture(endpoint,out,name,raw_log=False):
    command=['gh','api',endpoint]
    if raw_log:
        command.append('--allow-escape-sequences')
    result=subprocess.run(command,capture_output=True,timeout=90,check=True)
    require(len(result.stdout)<=64*1024**2 and len(result.stderr)<=16*1024**2, 'oversized API capture')
    for suffix,raw in (('.raw',result.stdout),('.stderr',result.stderr)):
        with (out/(name+suffix)).open('xb') as stream:
            stream.write(raw)
    save(out/(name+'.observation.json'),{'command':command,'observedExitCode':result.returncode,'timeoutSeconds':90,'stdoutSha256':hashlib.sha256(result.stdout).hexdigest(),'stderrSha256':hashlib.sha256(result.stderr).hexdigest(),'observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    return result.stdout

def control_log(raw):
    lines=[re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+','',line) for line in raw.decode('utf-8').splitlines()]
    starts=[i for i,line in enumerate(lines) if line=='##[group]Run actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd']
    matches=[]
    for start in starts:
        end=next((i for i in range(start+1,len(lines)) if lines[i].startswith('##[group]Run ')),len(lines))
        block=lines[start:end]
        fields={}
        for key in ('repository','ref','path'):
            values=[m.group(1).strip() for line in block if (m:=re.fullmatch(r'\s*'+key+r':\s*(.+)',line))]
            if len(values)==1:
                fields[key]=values[0]
        if fields.get('path')!='control':
            continue
        require(fields=={'repository':REPO,'ref':CONTROL,'path':'control'}, 'wrong/ambiguous original control inputs')
        probes=[i for i,line in enumerate(block) if re.fullmatch(r'(?:\[command\])?(?:/[^ ]*/)?git log -1 --format=%H',line)]
        require(len(probes)==1 and probes[0]+1<len(block) and block[probes[0]+1].strip().strip("'")==CONTROL, 'wrong original control git log')
        matches.append(CONTROL)
    require(matches==[CONTROL], 'missing/extra original control checkout')

def original_metadata(run,jobs,artifact):
    require(run['id']==RUN and run['head_sha']==HEAD and run['run_attempt']==1 and run['head_branch']==REF.removeprefix('refs/heads/') and run['path']==WORKFLOW and run['event']=='push', 'original run identity differs')
    require(run['repository']['full_name']==run['head_repository']['full_name']==REPO and run['status']=='completed' and run['conclusion']=='failure', 'original failed run replaced/relabeled')
    require(jobs['total_count']==len(jobs['jobs'])==4 and {j['name'] for j in jobs['jobs']}==set(JOBS), 'original jobs incomplete/extra')
    for job in jobs['jobs']:
        require(job['id']==JOBS[job['name']] and job['run_id']==RUN and job['head_sha']==HEAD and job['run_attempt']==1 and job['status']=='completed', 'original job identity differs')
        expected='failure' if job['name']=='publish' else 'success'
        require(job['conclusion']==expected, 'original job conclusion differs')
        step_names={'Sign fixed destination and retain original RPM/build predicates':'success','Attest original candidate build workflow provenance':'success','Verify destination-bound signature, SPDX, original provenance and policy':'failure'} if job['name']=='publish' else {'Checkout primary-reviewed control data only':'success'}
        for name,conclusion in step_names.items():
            steps=[s for s in job['steps'] if s['name']==name]
            require(len(steps)==1 and steps[0]['status']=='completed' and steps[0]['conclusion']==conclusion, 'original prerequisite/failure step differs')
    require(artifact['id']==ARTIFACT and artifact['name']=='r13-image-data' and artifact['size_in_bytes']==ZIP_SIZE and artifact['digest']=='sha256:'+ZIP_SHA and artifact['expired'] is False, 'immutable original ZIP metadata differs')
    ar=artifact['workflow_run']
    require(ar['id']==RUN and ar['head_sha']==HEAD and ar['head_branch']==REF.removeprefix('refs/heads/'), 'artifact original run/head differs')
    expiry=datetime.datetime.fromisoformat(artifact['expires_at'].replace('Z','+00:00'))
    require(expiry.tzinfo is not None and expiry>datetime.datetime.now(datetime.timezone.utc), 'original artifact expired')

def snapshot(out,prefix):
    run=decode(capture(f'repos/{REPO}/actions/runs/{RUN}',out,prefix+'-run'))
    jobs=decode(capture(f'repos/{REPO}/actions/runs/{RUN}/jobs?per_page=100',out,prefix+'-jobs'))
    inventory=decode(capture(f'repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100',out,prefix+'-artifacts'))
    require(inventory['total_count']==len(inventory['artifacts']), 'original artifact inventory incomplete')
    found=[a for a in inventory['artifacts'] if a['id']==ARTIFACT or a['name']=='r13-image-data']
    require(len(found)==1, 'original artifact missing/ambiguous')
    original_metadata(run,jobs,found[0])
    return found[0]

def download(out):
    path=out/'original-image.zip'
    bound=sum(LIMITS.values())+1024**2
    require(shutil.disk_usage(out).free>ZIP_SIZE+256*1024**2, 'insufficient ZIP scratch capacity')
    command=['gh','api',f'repos/{REPO}/actions/artifacts/{ARTIFACT}/zip','--allow-escape-sequences']
    deadline=time.monotonic()+900
    count=0
    with path.open('xb') as target,(out/'original-image-download.stderr').open('xb') as error:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=error)
        selector=selectors.DefaultSelector()
        try:
            selector.register(process.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                require(remaining>0, 'ZIP download deadline elapsed')
                if not selector.select(min(5,remaining)):
                    continue
                chunk=os.read(process.stdout.fileno(),1024**2)
                if not chunk:
                    break
                count+=len(chunk)
                require(count<=bound, 'oversized ZIP download')
                target.write(chunk)
            require(process.wait(timeout=15)==0, 'ZIP transport failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
            process.stdout.close()
            selector.close()
    require(file_sha(path)==ZIP_SHA, 'whole immutable original ZIP digest differs')
    save(out/'original-image-zip-receipt.json',{'artifactId':ARTIFACT,'originalRunId':RUN,'apiSize':ZIP_SIZE,'downloadedSize':count,'zipSha256':ZIP_SHA,'command':command,'observedExitCode':0,'downloadTimeoutSeconds':900})
    data=out/'data'
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        require(len(infos)==len(LIMITS) and {i.filename for i in infos}==set(LIMITS), 'ZIP inventory missing/extra/duplicate')
        for info in infos:
            mode=info.external_attr>>16
            require(not info.is_dir() and '/' not in info.filename and '\\' not in info.filename and stat.S_IFMT(mode) in (0,stat.S_IFREG), 'unsafe ZIP entry')
            require(not info.flag_bits&1 and 0<info.file_size<=LIMITS[info.filename], 'ZIP entry size/encryption differs')
        require(shutil.disk_usage(out).free>sum(i.file_size for i in infos)+256*1024**2, 'insufficient extraction scratch capacity')
        data.mkdir(mode=0o700)
        for info in infos:
            count=0
            with archive.open(info) as source,(data/info.filename).open('xb') as target:
                while chunk:=source.read(1024**2):
                    count+=len(chunk)
                    require(count<=info.file_size, 'oversized ZIP member')
                    target.write(chunk)
                require(count==info.file_size, 'truncated ZIP member')
    # Only this job's newly created verified scratch ZIP is discarded. The exact
    # remote original artifact remains unchanged; its digest/receipt is retained.
    path.unlink()
    return data

def prepare_trust(data,out):
    policy=decode((data/'policy.json').read_bytes())
    require(policy['default']==[{'type':'reject'}], 'default trust policy weakened')
    rule=policy['transports']['docker']['ghcr.io/bearyjd/bazzite-tower']
    require(rule==[{'type':'sigstoreSigned','keyPath':'/etc/pki/containers/bazzite-tower-cosign.pub','signedIdentity':{'type':'matchRepository'}}], 'repository trust policy weakened')
    trust=out/'trust'
    trust.mkdir()
    (trust/'registries.d').mkdir()
    rule[0]['keyPath']=str(data/'cosign.pub')
    save(trust/'policy.json',{'default':[{'type':'reject'}],'transports':{'docker':{DESTINATION:rule}}})
    with (trust/'registries.d/repository.yaml').open('x') as stream:
        stream.write('docker:\n  '+DESTINATION+':\n    use-sigstore-attachments: true\n')

def predicates(data,out):
    for name,filename,kind in (('sbom','sbom.json','https://spdx.dev/Document'),('provenance','build-provenance.json','https://github.com/bearyjd/bazzite-tower/attestations/r13-candidate-build/v1'),('sign-v1',None,'https://sigstore.dev/cosign/sign/v1')):
        verified=[decode(line) for line in (out/(name+'.verified.json')).read_bytes().splitlines() if line.strip()]
        expected=decode((data/filename).read_bytes()) if filename else None
        statements=[decode(base64.b64decode(item['payload'],validate=True)) for item in verified]
        require(any(v['predicateType']==kind and (filename is None or v['predicate']==expected) and any(s['digest']=={'sha256':DIGEST[7:]} and (filename is None or s['name']==DESTINATION) for s in v['subject']) for v in statements), 'typed original predicate/subject/digest mismatch: '+name)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('acquire','predicates','final-original-check'))
    parser.add_argument('--control',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    code=Path(__file__).resolve().parent.parent
    context(code)
    pub=validator(args.control)
    out=args.out
    require(out.is_absolute() and out.parent==Path(os.environ['RUNNER_TEMP']) and not any(p.is_symlink() for p in (out,*out.parents)), 'unsafe runner scratch path')
    if args.mode=='acquire':
        require(not out.exists(), 'scratch must be new; no reuse')
        out.mkdir(mode=0o700)
        artifact=snapshot(out,'before')
        for name in ('approval','build','validate'):
            control_log(capture(f'repos/{REPO}/actions/jobs/{JOBS[name]}/logs',out,name+'-original-control',True))
        data=download(out)
        candidate=pub.validate_candidate(args.control,data,HEAD,RUN)
        require(candidate['image']['manifestDigest']==DIGEST, 'original immutable OCI digest differs')
        for name,value in PINNED_FILES.items():
            require(file_sha(data/name)==value, 'pinned original predicate/OCI bytes differ')
        pins=decode(pub.read(args.control/'build_files/firewall/snitchwatch-system-pins.json'))
        bridge=decode(pub.read(data/'bridge-manifest.json'))
        require(bridge['flatpak']=={'appId':'org.snitchwatch.Snitchwatch','profile':'system','runtimeRef':pins['flatpakRuntimeRef'],'sourceCommit':candidate['identities']['sourceCommit'],'manifestSha256':pins['sourceFiles']['packaging/flatpak/org.snitchwatch.Snitchwatch.system.yml'],'finishArgs':pins['flatpakFinishArgs']}, 'original installed GUI contract differs')
        require(snapshot(out,'after')==artifact, 'original artifact changed during acquisition')
        prepare_trust(data,out)
        save(out/'original-image-data-validation.json',{'status':'ORIGINAL_IMAGE_DATA_VALIDATED_POLICY_PENDING','originalHead':HEAD,'originalRunId':RUN,'originalRunAttempt':1,'originalControlHead':CONTROL,'artifactId':ARTIFACT,'artifactZipSha256':ZIP_SHA,'candidateMetadataSha256':file_sha(data/'candidate.json'),'image':candidate['image'],'recoveryHead':os.environ['GITHUB_SHA'],'recoveryRunId':int(os.environ['GITHUB_RUN_ID']),'runtime':'UNVERIFIED','guestStageAuthorized':False})
    elif args.mode=='predicates':
        predicates(out/'data',out)
    else:
        snapshot(out,'before-pointer')

if __name__=='__main__':
    main()
