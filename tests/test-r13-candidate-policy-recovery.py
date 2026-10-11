#!/usr/bin/env python3
"""Offline recovery checks: authentic original snapshots; no signing/network."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import yaml
import zipfile

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('recovery',ROOT/'ci/r13-candidate-policy-recovery.py')
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

class Recovery(unittest.TestCase):
    def metadata(self):
        run={'id':r.RUN,'head_sha':r.HEAD,'run_attempt':1,'head_branch':r.REF.removeprefix('refs/heads/'),'path':r.WORKFLOW,'event':'push','repository':{'full_name':r.REPO},'head_repository':{'full_name':r.REPO},'status':'completed','conclusion':'failure'}
        jobs={'total_count':4,'jobs':[]}
        for name,ident in r.JOBS.items():
            steps={'Checkout primary-reviewed control data only':'success'} if name!='publish' else {'Sign fixed destination and retain original RPM/build predicates':'success','Attest original candidate build workflow provenance':'success','Verify destination-bound signature, SPDX, original provenance and policy':'failure'}
            jobs['jobs'].append({'id':ident,'run_id':r.RUN,'head_sha':r.HEAD,'run_attempt':1,'status':'completed','conclusion':'failure' if name=='publish' else 'success','name':name,'steps':[{'name':key,'status':'completed','conclusion':value} for key,value in steps.items()]})
        artifact={'id':r.ARTIFACT,'name':'r13-image-data','size_in_bytes':r.ZIP_SIZE,'digest':'sha256:'+r.ZIP_SHA,'expired':False,'expires_at':'2099-01-01T00:00:00Z','workflow_run':{'id':r.RUN,'head_sha':r.HEAD,'head_branch':r.REF.removeprefix('refs/heads/')}}
        return run,jobs,artifact

    def test_exact_original_failure_remains_failure(self):
        r.original_metadata(*self.metadata())

    def test_original_run_cannot_be_recovery_or_relabeled_green(self):
        for key,value in (('id',123),('head_sha','a'*40),('run_attempt',2),('head_branch','ci/r13-candidate-policy-recovery'),('path',r.RECOVERY_WORKFLOW),('event','workflow_dispatch'),('conclusion','success')):
            run,jobs,a=self.metadata()
            run[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                r.original_metadata(run,jobs,a)

    def test_failed_build_or_wrong_original_job_refused(self):
        for key,value in (('id',1),('run_id',1),('head_sha','a'*40),('run_attempt',2),('conclusion','failure')):
            run,jobs,a=self.metadata()
            jobs['jobs'][1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                r.original_metadata(run,jobs,a)

    def test_original_named_step_cannot_be_skipped(self):
        run,jobs,a=self.metadata()
        jobs['jobs'][-1]['steps'][1]['conclusion']='skipped'
        with self.assertRaises(ValueError):
            r.original_metadata(run,jobs,a)

    def test_zip_expiry_size_digest_and_identity_refused(self):
        for key,value in (('id',1),('digest','sha256:'+'0'*64),('size_in_bytes',1),('expired',True),('expires_at','2000-01-01T00:00:00Z')):
            run,jobs,a=self.metadata()
            a[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                r.original_metadata(run,jobs,a)

    def test_new_recovery_context_cannot_impersonate_original(self):
        env={'R13_RECOVERY_HEAD':'a'*40,'GITHUB_REPOSITORY':r.REPO,'GITHUB_EVENT_NAME':'push','GITHUB_REF':r.RECOVERY_REF,'GITHUB_WORKFLOW_REF':r.REPO+'/'+r.RECOVERY_WORKFLOW+'@'+r.RECOVERY_REF,'GITHUB_SHA':'a'*40,'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'999'}
        with patch.dict(os.environ,env),patch.object(r.subprocess,'check_output',side_effect=['a'*40+'\n',b'']):
            r.context(ROOT)
        for key,value in (('GITHUB_SHA',r.HEAD),('GITHUB_RUN_ID',str(r.RUN)),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_EVENT_NAME','workflow_dispatch')):
            with self.subTest(key=key),patch.dict(os.environ,{**env,key:value}),self.assertRaises(ValueError):
                r.context(ROOT)

    def test_control_log_isolated_from_source(self):
        block=lambda head,path:('##[group]Run actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd\n repository: '+r.REPO+'\n ref: '+head+'\n path: '+path+'\n[command]/usr/bin/git log -1 --format=%H\n'+head+'\n').encode()
        raw=block(r.CONTROL,'control')+block(r.HEAD,'source')
        r.control_log(raw)
        for changed in (raw.replace(r.CONTROL.encode(),r.HEAD.encode()),raw+raw,raw.replace(b'path: control',b'path: source')):
            with self.assertRaises(ValueError):
                r.control_log(changed)

    def predicate_fixture(self,out):
        data=out/'data'
        data.mkdir()
        expected={'sbom':{'spdxVersion':'SPDX-2.3','packages':[{'name':'original'}]},'provenance':{'originalHead':r.HEAD,'originalRun':r.RUN}}
        for name,filename,kind in (('sbom','sbom.json','https://spdx.dev/Document'),('provenance','build-provenance.json','https://github.com/bearyjd/bazzite-tower/attestations/r13-candidate-build/v1'),('sign-v1',None,'https://sigstore.dev/cosign/sign/v1')):
            if filename:
                (data/filename).write_text(json.dumps(expected[name]))
            v={'predicateType':kind,'predicate':expected.get(name,{}),'subject':[{'name':r.DESTINATION if filename else '', 'digest':{'sha256':r.DIGEST[7:]}}]}
            (out/(name+'.verified.json')).write_text(json.dumps({'payload':base64.b64encode(json.dumps(v).encode()).decode()})+'\n')
        return data

    def test_typed_original_predicates_and_bundle_sign_match(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            r.predicates(self.predicate_fixture(out),out)

    def test_original_predicate_subject_digest_and_sign_type_substitution_refused(self):
        for name,mutation in (('provenance',lambda v:v['predicate'].update(originalHead='a'*40)),('sbom',lambda v:v['subject'][0].update(name='ghcr.io/foreign/image')),('sign-v1',lambda v:v.update(predicateType='https://spdx.dev/Document')),('sign-v1',lambda v:v['subject'][0].update(digest={'sha256':'0'*64}))):
            with tempfile.TemporaryDirectory() as d:
                out=Path(d)
                data=self.predicate_fixture(out)
                path=out/(name+'.verified.json')
                envelope=json.loads(path.read_text())
                statement=json.loads(base64.b64decode(envelope['payload']))
                mutation(statement)
                envelope['payload']=base64.b64encode(json.dumps(statement).encode()).decode()
                path.write_text(json.dumps(envelope)+'\n')
                with self.subTest(name=name),self.assertRaises(ValueError):
                    r.predicates(data,out)

    def zip_fixture(self,kind='valid'):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name in r.LIMITS:
                info=zipfile.ZipInfo(name)
                info.external_attr=(0o120777 if kind=='symlink' and name=='candidate.json' else 0o100644)<<16
                archive.writestr(info,b'fixture')
            if kind=='extra':
                archive.writestr('../escape',b'outside')
        return stream.getvalue()

    def download_fixture(self,out,raw,expected_sha=None):
        process=Mock(stdout=Mock(fileno=Mock(return_value=7)),wait=Mock(return_value=0),poll=Mock(return_value=0))
        selector=Mock(select=Mock(return_value=[(None,None)]))
        with patch.object(r,'ZIP_SHA',expected_sha or hashlib.sha256(raw).hexdigest()),patch.object(r.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),patch.object(r.subprocess,'Popen',return_value=process),patch.object(r.selectors,'DefaultSelector',return_value=selector),patch.object(r.os,'read',side_effect=[raw,b'']):
            return r.download(out)

    def test_whole_zip_verified_exact_files_retained_only_scratch_archive_removed(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            data=self.download_fixture(out,self.zip_fixture())
            self.assertEqual({p.name for p in data.iterdir()},set(r.LIMITS))
            self.assertTrue((out/'original-image-zip-receipt.json').is_file())
            self.assertFalse((out/'original-image.zip').exists())

    def test_zip_digest_mismatch_never_extracts(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            with self.assertRaisesRegex(ValueError,'ZIP digest'):
                self.download_fixture(out,self.zip_fixture(),'0'*64)
            self.assertFalse((out/'data').exists())

    def test_symlink_or_extra_path_zip_never_extracts(self):
        for kind in ('symlink','extra'):
            with tempfile.TemporaryDirectory() as d:
                out=Path(d)
                with self.subTest(kind=kind),self.assertRaises(ValueError):
                    self.download_fixture(out,self.zip_fixture(kind))
                self.assertFalse((out/'data').exists())

    def test_original_bad_keyed_subject_prevents_candidate_pointer(self):
        workflow=yaml.safe_load((ROOT/r.RECOVERY_WORKFLOW).read_text())
        steps=workflow['jobs']['recover-policy']['steps']
        verify=next(s['run'] for s in steps if s.get('name','').startswith('Verify keyed sign-v1'))
        self.assertTrue(verify.startswith('set -euo pipefail'))
        self.assertIn('predicates --control control',verify)
        self.assertIn('skopeo --policy "$receipt/trust/policy.json"',verify)
        self.assertIn('--source-digest "$ORIGINAL_HEAD"',verify)

    def test_recovery_trigger_cannot_rebuild_original_image(self):
        workflow=yaml.safe_load((ROOT/r.RECOVERY_WORKFLOW).read_text())
        self.assertEqual(workflow['on'],{'push':{'branches':['ci/r13-candidate-policy-recovery']}})
        self.assertEqual(workflow['permissions'],{})
        self.assertEqual(workflow['concurrency'],{'group':'bazzite-tower-stable-writers','cancel-in-progress':False})
        job=workflow['jobs']['recover-policy']
        self.assertIn('github.sha == vars.R13_RECOVERY_HEAD',job['if'])
        self.assertEqual(job['permissions'],{'contents':'read','actions':'read','packages':'write','attestations':'read'})
        steps=job['steps']
        runs='\n'.join(s.get('run','') for s in steps)
        self.assertNotIn('podman build',runs)
        self.assertNotIn('docker build',runs)
        self.assertNotIn('attest-build-provenance','\n'.join(s.get('uses','') for s in steps))
        self.assertIn('--source-digest "$ORIGINAL_HEAD"',runs)
        self.assertNotIn('--source-digest "$GITHUB_SHA"',runs)
        self.assertIn('cosign sign -y --key env://COSIGN_PRIVATE_KEY',runs)
        self.assertIn('cosign sign -y --new-bundle-format --key env://COSIGN_PRIVATE_KEY',runs)
        self.assertIn('--type https://sigstore.dev/cosign/sign/v1',runs)
        self.assertNotIn('--insecure',runs)
        verify=next(i for i,s in enumerate(steps) if s.get('name','').startswith('Verify keyed sign-v1'))
        pointer=next(i for i,s in enumerate(steps) if s.get('name','').startswith('Publish fixed candidate pointer'))
        self.assertLess(verify,pointer)
        original=next(i for i,s in enumerate(steps) if s.get('name','').startswith('Verify existing original b2'))
        stage=next(i for i,s in enumerate(steps) if s.get('name','').startswith('Stage original unchanged'))
        self.assertLess(original,stage)
        for step in steps:
            if 'uses' in step:
                self.assertRegex(step['uses'],r'@[0-9a-f]{40}$')

if __name__=='__main__':
    unittest.main()
