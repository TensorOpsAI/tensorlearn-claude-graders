"""Deterministic bug-fix assessment. Docker is the security boundary, not node:vm.

The parent compares observed values to expectations kept outside the container.
Student test exit behavior is differential evidence, not proof of assertion
quality, authorship, or the order in which the student wrote tests and code.
"""
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import tempfile
import time

class Unavailable(RuntimeError):
    pass

class CandidateFailure(ValueError):
    pass

IMAGE = re.compile(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}\Z')
BASELINE = """// Contract: docs/contract.md. Pure function; do not mutate claims.
export function getClaim(claims, viewerId, claimId) {
  const target = String(claimId ?? '').trim();
  const viewer = String(viewerId ?? '').trim();
  return claims.find(claim => String(claim.id).trim() === target && String(claim.ownerId).trim().startsWith(viewer)) ?? null;
}
"""
HARNESS = r"""import fs from 'node:fs';
import vm from 'node:vm';
const stringify = JSON.stringify;
const emit = process.stdout.write.bind(process.stdout);
const source = fs.readFileSync('/workspace/src/claims.mjs', 'utf8');
const inputs = fs.readFileSync('/trusted/inputs.json', 'utf8');
const context = vm.createContext(Object.create(null), {codeGeneration:{strings:false,wasm:false}});
// No process, filesystem, network, console, require or import capability is
// supplied to the pure-function module. Docker still contains VM escapes.
const module = new vm.SourceTextModule(source, {context,
  importModuleDynamically(){ throw new Error('Imports unavailable'); }});
await module.link(() => { throw new Error('Imports unavailable'); });
await module.evaluate({timeout:1000});
context.getClaim = module.namespace.getClaim;
new vm.Script('globalThis.inputs = JSON.parse(' + stringify(inputs) + ')').runInContext(context, {timeout:1000});
const output = new vm.Script(`inputs.map(input => {
  try { const value = getClaim(input.claims, input.viewer, input.claim);
    return {value, after:input.claims}; }
  catch { return {error:true}; }
})`).runInContext(context, {timeout:2000});
emit(stringify(output));
"""

def clean_env():
    return {'PATH':'/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin','LANG':'C.UTF-8'}

def cases():
    record = {'id':'claim-B','ownerId':'user-42','status':'ready'}
    rows = [
        ('exact-owner',[record],'user-42','claim-B',record),
        ('prefix-owner',[record],'user-4','claim-B',None),
        ('empty-owner',[record],'','claim-B',None),
        ('whitespace-owner',[record],'  ','claim-B',None),
        ('missing-claim',[record],'user-42','absent',None),
        ('numeric-viewer',[{'id':'c','ownerId':'42'}],42,'c',None),
        ('numeric-claim',[{'id':'7','ownerId':'u'}],'u',7,None),
        ('null-viewer',[record],None,'claim-B',None),
        ('boolean-viewer',[{'id':'c','ownerId':'true'}],True,'c',None),
        ('null-claim',[{'id':'null','ownerId':'u'}],'u',None,None),
        ('boolean-claim',[{'id':'false','ownerId':'u'}],'u',False,None),
        ('object-viewer',[{'id':'c','ownerId':'[object Object]'}],{},'c',None),
        ('object-claim',[{'id':'[object Object]','ownerId':'u'}],'u',{},None),
        ('malformed-record-id',[{'id':7,'ownerId':'u'}],'u','7',None),
        ('malformed-record-owner',[{'id':'c','ownerId':42}],'42','c',None),
        ('empty-record-id',[{'id':' ','ownerId':'u'}],'u',' ',None),
        ('null-record-owner',[{'id':'c','ownerId':None}],'null','c',None),
        ('null-record',[None,record],'user-42','claim-B',record),
        ('non-array',{},'u','c',None),
        ('null-array',None,'u','c',None),
        ('case-sensitive',[record],'USER-42','claim-B',None),
        ('leading-zero',[{'id':'07','ownerId':'u'}],'u','7',None),
        ('trim-identifiers',[{'id':' c ','ownerId':' u ','status':'ready'}],' u ',' c ',{'id':' c ','ownerId':' u ','status':'ready'}),
        ('duplicate-id-other-owner',[{'id':'c','ownerId':'v'},{'id':'c','ownerId':'u'}],'u','c',{'id':'c','ownerId':'u'}),
    ]
    return [{'id':key,'input':{'claims':claims,'viewer':viewer,'claim':claim},'expected':expected}
            for key,claims,viewer,claim,expected in rows]

def bounded(command, timeout=20):
    try:
        child = subprocess.Popen(command, env=clean_env(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise Unavailable('Docker worker could not start') from error
    streams = {'stdout':bytearray(),'stderr':bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
    end = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= end:
                raise CandidateFailure('Worker time limit exceeded')
            for event, _ in selector.select(.1):
                data = os.read(event.fileobj.fileno(),4096)
                if not data:
                    selector.unregister(event.fileobj)
                    continue
                streams[event.data].extend(data)
                if sum(map(len,streams.values())) > 65536:
                    raise CandidateFailure('Worker output limit exceeded')
        return child.wait(timeout=1), bytes(streams['stdout']), bytes(streams['stderr'])
    finally:
        selector.close()
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        child.stderr.close()

def worker(workspace, trusted, image, argv):
    name = 'tensorlearn-assess-' + secrets.token_hex(8)
    command = ['docker','run','--rm','--pull=never','--name',name,'--network=none','--read-only',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--user=65534:65534',
        '--pids-limit=64','--memory=256m','--cpus=1','--ulimit=nofile=128:128',
        '--ulimit=fsize=1024:1024','--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m',
        '--workdir=/workspace','--mount',f'type=bind,src={workspace},dst=/workspace,readonly',
        '--mount',f'type=bind,src={trusted},dst=/trusted,readonly',
        '--env=HOME=/tmp','--env=NODE_OPTIONS=','--entrypoint=node',image] + argv
    try:
        code, out, err = bounded(command)
        if code == 125:
            raise Unavailable('Docker could not create the isolated worker')
        return code, out, err
    finally:
        try:
            removed = subprocess.run(['docker','rm','-f',name],env=clean_env(),
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            if removed.returncode:
                # --rm may already have deleted it. A successful empty inventory
                # distinguishes that case from a lost Docker connection.
                remaining = subprocess.run(['docker','ps','-a','--filter',f'name=^/{name}$','--format','{{.ID}}'],
                    env=clean_env(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
                if remaining.returncode or remaining.stdout.strip():
                    raise Unavailable('Worker cleanup could not be confirmed')
        except (OSError,subprocess.TimeoutExpired) as error:
            raise Unavailable('Worker cleanup could not be confirmed') from error

def snapshot(workspace, destination, baseline=False):
    workspace=workspace.resolve(strict=True)
    source = workspace / 'src/claims.mjs'
    if (source.parent.is_symlink() or source.is_symlink() or not source.is_file()
        or source.stat().st_size > 65536):
        raise CandidateFailure('Missing or unsafe claims source')
    (destination / 'src').mkdir(parents=True)
    (destination / 'src/claims.mjs').write_text(BASELINE if baseline else source.read_text())
    tests = []
    directory = workspace / 'tests/student'
    if directory.parent.is_symlink() or directory.is_symlink():
        raise CandidateFailure('Unsafe student tests directory')
    total=count=0
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise CandidateFailure('Student tests cannot contain symlinks')
        if not path.is_file():
            continue
        count+=1
        total+=path.stat().st_size
        if path.stat().st_size > 65536 or count > 64 or total > 1_000_000:
            raise CandidateFailure('Student test files exceed limits')
        target = destination / path.relative_to(workspace)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
        if path.name.endswith('.test.mjs'):
            tests.append(str(path.relative_to(workspace)))
    return tests

def decode_results(data):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:
                raise ValueError('Duplicate result keys')
            value[key]=item
        return value
    try:
        return json.loads(data.decode('utf-8'),object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite')))
    except (ValueError,UnicodeError) as error:
        raise CandidateFailure('Worker did not produce one strict JSON result') from error

def assess(workspace, image):
    if not IMAGE.fullmatch(image):
        raise Unavailable('A digest-pinned Docker image is required')
    try:
        available = subprocess.run(['docker','image','inspect',image],env=clean_env(),
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise Unavailable('Docker is unavailable') from error
    if available.returncode:
        raise Unavailable('Pinned image must be preloaded; no implicit image pull')
    matrix = cases()
    with tempfile.TemporaryDirectory(prefix='tensorlearn-bug-assessment-') as folder:
        root=Path(folder)
        trusted=root/'trusted';trusted.mkdir()
        (trusted/'probe.mjs').write_text(HARNESS)
        (trusted/'inputs.json').write_text(json.dumps([x['input'] for x in matrix]))
        candidate=root/'candidate';original=root/'original'
        try:
            tests=snapshot(Path(workspace),candidate)
            snapshot(Path(workspace),original,baseline=True)
            code,out,_=worker(candidate,trusted,image,['--experimental-vm-modules','/trusted/probe.mjs'])
            values=decode_results(out) if code==0 else None
            valid=isinstance(values,list) and len(values)==len(matrix)
            checks=[]
            for index,item in enumerate(matrix):
                value=values[index] if valid else None
                checks.append({'id':item['id'],'passed':isinstance(value,dict) and set(value)=={'value','after'}
                    and value['value']==item['expected'] and value['after']==item['input']['claims']})
            before=after=None
            if tests:
                before=worker(original,trusted,image,['--test',*tests])[0]
                after=worker(candidate,trusted,image,['--test',*tests])[0]
            checks.append({'id':'student-regression-differential','passed':bool(tests) and before!=0 and after==0})
            return checks
        except CandidateFailure:
            return [{'id':x['id'],'passed':False} for x in matrix]+[{'id':'student-regression-differential','passed':False}]
