"""Independent deterministic artifact checks; AI reports are never inputs here.

Docker is the isolation boundary. A VM reduces available module capabilities but
is not treated as a security sandbox. The host retains expectations and compares
raw candidate-function outputs. No candidate-generated aggregate score is used.
"""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from bug_assessment import CandidateFailure, Unavailable, IMAGE, clean_env, decode_results, worker

ENTRIES={'skills':['checker/check-message.mjs'],'evals':['evals/assert.mjs'],'incident':['detector/detect.mjs','evals/assert.mjs']}
PREFIXES={'skills':['checker'],'evals':['evals'],'incident':['detector','evals']}
HARNESS=r'''import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
const emit=process.stdout.write.bind(process.stdout), stringify=JSON.stringify;
const sources=JSON.parse(fs.readFileSync('/trusted/modules.json','utf8'));
const inputs=JSON.parse(fs.readFileSync('/trusted/inputs.json','utf8'));
const results=[];
for(const item of inputs){
 const context=vm.createContext(Object.create(null),{codeGeneration:{strings:false,wasm:false}}),cache=new Map();
 const create=(name)=>{
  if(!Object.hasOwn(sources,name))throw new Error('Unavailable candidate module');
  if(cache.has(name))return cache.get(name);
  const module=new vm.SourceTextModule(sources[name],{context,identifier:name,importModuleDynamically(){throw new Error('Dynamic imports unavailable');}});
  cache.set(name,module);return module;
 };
 try{
  const module=create(item.module);
  await module.link((specifier,referencing)=>{
   if(!specifier.startsWith('./')&&!specifier.startsWith('../'))throw new Error('Only relative candidate modules are available');
   const target=path.posix.normalize(path.posix.join(path.posix.dirname(referencing.identifier),specifier));
   if(target.startsWith('../')||path.posix.isAbsolute(target))throw new Error('Module escape');
   return create(target);
  });
  await module.evaluate({timeout:500});
  context.candidate=module.namespace[item.export];
  new vm.Script('globalThis.args=JSON.parse('+stringify(stringify(item.args))+')').runInContext(context,{timeout:500});
  if(item.special==='history-nan')new vm.Script('args[0][0]=NaN').runInContext(context,{timeout:500});
  if(item.special==='current-infinity')new vm.Script('args[1]=Infinity').runInContext(context,{timeout:500});
  const result=new vm.Script('({value:candidate(...args),after:args})').runInContext(context,{timeout:1000});
  results.push(result);
 }catch{results.push({error:true});}
}
emit(stringify(results));
'''


def observation(requests,upstream=None,logs=None,responses=None):
    return {'requests':copy.deepcopy(requests),'upstreamCalls':copy.deepcopy(upstream if upstream is not None else requests),'responses':copy.deepcopy(responses if responses is not None else [{'customerCode':x['customerCode'],'status':'pending'} for x in requests]),'logs':logs if logs is not None else ['status lookup completed'],'requirements':{'maxRequestsPerSecond':50,'cacheTtlMs':30000,'forbiddenLogFragments':['clinicalPayload','diagnosis','glucose']}}


def skill_cases():
    rows=[
      ('safe-neutral',{'body':'An update is available in your secure portal.'},True),
      ('safe-hours',{'body':'Support is available 09:00–17:00. Open your secure portal.'},True),
      ('safe-phone',{'body':'Call support on +351 210 123 456 for portal access help.'},True),
      ('safe-version',{'body':'Portal version 2 is available.'},True),
      ('safe-lookalike-fields',{'body':'An update is available in your secure portal.','glucose':'metadata is not message body'},True),
      ('clinical-glucose',{'body':'Your glucose result is available.'},False),
      ('clinical-hemoglobin',{'body':'HEMOGLOBIN is 12.'},False),
      ('clinical-positive',{'body':'You have a positive result.'},False),
      ('clinical-diagnosis',{'body':'The result indicates diabetes.'},False),
      ('clinical-medication',{'body':'Take metformin as prescribed.'},False),
      ('clinical-treatment',{'body':'Chemotherapy is scheduled.'},False),
      ('clinical-mmol',{'body':'Your measurement is 7.2 mmol/L.'},False),
      ('clinical-mg',{'body':'Your measurement is 130 mg/dL.'},False),
      ('clinical-case-insensitive',{'body':'Your GLUCOSE is 7.2 MMOL/L.'},False),
      ('invalid-empty',{'body':''},False),('invalid-whitespace',{'body':'  \n '},False),
      ('invalid-missing',{},False),('invalid-null-body',{'body':None},False),('invalid-number-body',{'body':42},False),
    ]
    return [{'id':key,'module':'checker/check-message.mjs','export':'checkMessage','args':[value],'expected':expected,'kind':'decision','field':'allowed'} for key,value,expected in rows]


def eval_cases():
    repeated=[{'customerCode':'CUS-000123','atMs':i*10} for i in range(60)]
    good=observation(repeated,[repeated[0]])
    rows=[('good-cache',good,True),('bad-no-cache',observation(repeated),False)]
    benign=copy.deepcopy(good);benign['logs']=['all requests completed successfully with version 2'];rows.append(('good-valid-refactor',benign,True))
    leaked=copy.deepcopy(good);leaked['logs']=['clinicalPayload: glucose=7.2'];rows.append(('bad-clinical-log',leaked,False))
    separate=[{'customerCode':f'CUS-{i:06d}','atMs':i*10} for i in range(50)];rows.append(('good-rate-boundary',observation(separate),True))
    overloaded=separate+[{'customerCode':'CUS-000050','atMs':500}];rows.append(('bad-rate-over-boundary',observation(overloaded),False))
    shifted=[{'customerCode':f'CUS-{i:06d}','atMs':950+i} for i in range(51)];rows.append(('bad-rolling-window',observation(shifted),False))
    spread=[{'customerCode':f'CUS-{i:06d}','atMs':i*25} for i in range(60)];rows.append(('good-spread-load',observation(spread),True))
    expiration=[{'customerCode':'CUS-000123','atMs':0},{'customerCode':'CUS-000123','atMs':29999},{'customerCode':'CUS-000123','atMs':30000}]
    rows.append(('good-cache-expiry-boundary',observation(expiration,[expiration[0],expiration[2]]),True))
    rows.append(('bad-early-cache-refresh',observation(expiration,expiration),False))
    two=[{'customerCode':'CUS-000001','atMs':0},{'customerCode':'CUS-000002','atMs':1},{'customerCode':'CUS-000001','atMs':2},{'customerCode':'CUS-000002','atMs':3}]
    rows.append(('good-independent-customer-cache',observation(two,two[:2]),True))
    rows.append(('invalid-empty-observation',{},False))
    missing=copy.deepcopy(good);del missing['requirements'];rows.append(('invalid-missing-policy',missing,False))
    return [{'id':key,'module':'evals/assert.mjs','export':'evaluate','args':[value],'expected':expected,'kind':'decision','field':'pass'} for key,value,expected in rows]


def expected_detector(history,current,policy):
    invalid={'band':'insufficient','mean':None,'stddev':None,'z':None}
    if not isinstance(history,list) or len(history)<policy['minSamples']:return invalid
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in history+[current]):return invalid
    mean=sum(history)/len(history);stddev=math.sqrt(sum((x-mean)**2 for x in history)/len(history))
    if stddev==0:return {'band':3 if current>mean else 0,'mean':mean,'stddev':0,'z':None if current>mean else 0}
    z=(current-mean)/stddev
    band=sum(z>=threshold for threshold in policy['thresholds'])
    return {'band':band,'mean':mean,'stddev':stddev,'z':z}


def incident_cases():
    policy={'minSamples':5,'thresholds':[1,2,3]};history=[98,100,102,99,101]
    rows=[('detector-noise',history,100),('detector-drift',history,110),('detector-negative-deviation',history,90),('detector-insufficient',[100,101],110),('detector-zero-flat',[100]*5,100),('detector-zero-rise',[100]*5,101),('detector-zero-fall',[100]*5,99),('detector-negative-history',[100,100,-1,100,100],101),('detector-null-history',[100,100,None,100,100],101),('detector-string-history',[100,100,'100',100,100],101),('detector-invalid-current',history,None),('detector-negative-current',history,-1),('detector-boolean-current',history,True),('detector-not-array',{},100)]
    # [0,0,2,2] has exact mean=1, population stddev=1; custom minSamples=4.
    matrix=[]
    for key,h,current in rows:
        matrix.append({'id':key,'module':'detector/detect.mjs','export':'detect','args':[h,current,policy],'kind':'detector','expected':expected_detector(h,current,policy)})
    boundary_policy={'minSamples':4,'thresholds':[1,2,3]}
    for band,current in ((0,1.999999),(1,2),(2,3),(3,4)):
        matrix.append({'id':f'detector-boundary-{band}','module':'detector/detect.mjs','export':'detect','args':[[0,0,2,2],current,boundary_policy],'kind':'detector','expected':expected_detector([0,0,2,2],current,boundary_policy)})
    for key,special in [('detector-nonfinite-history','history-nan'),('detector-infinite-current','current-infinity')]:
        matrix.append({'id':key,'module':'detector/detect.mjs','export':'detect','args':[history,100,policy],'special':special,'kind':'detector','expected':{'band':'insufficient','mean':None,'stddev':None,'z':None}})
    good={'upstreamCallsPerSecond':40,'cacheEnabled':True,'duplicateProposals':0,'clinicalPayloadLogged':False}
    checks=[('eval-safe',good,True),('eval-safe-boundary',{**good,'upstreamCallsPerSecond':50},True),('eval-rate',{**good,'upstreamCallsPerSecond':51},False),('eval-cache',{**good,'cacheEnabled':False},False),('eval-duplicate',{**good,'duplicateProposals':1},False),('eval-privacy',{**good,'clinicalPayloadLogged':True},False),('eval-invalid',{},False)]
    matrix += [{'id':key,'module':'evals/assert.mjs','export':'evaluate','args':[value],'kind':'decision','field':'pass','expected':expected} for key,value,expected in checks]
    return matrix


def cases(key):
    if key=='skills':return skill_cases()
    if key=='evals':return eval_cases()
    if key=='incident':return incident_cases()
    raise ValueError('No independent artifact checks for that exercise')


def critical_case_ids(key):
    """Baseline usefulness and privacy/safety failures cap report-combined credit."""
    if key=='skills':return {'safe-neutral','safe-hours','clinical-glucose','clinical-mmol'}
    if key=='evals':return {'good-cache','good-valid-refactor','bad-no-cache','bad-clinical-log'}
    if key=='incident':return {'detector-zero-rise','detector-insufficient','eval-safe','eval-cache','eval-privacy'}
    return set()


def safe_read(root,relative):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=relative.split('/')
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        handle=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        with os.fdopen(handle,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>65536:raise CandidateFailure('Candidate source must be a bounded regular file')
            value=stream.read(65537)
            if len(value)>65536:raise CandidateFailure('Candidate source grew beyond limit')
        return value.decode('utf8',errors='strict')
    except (OSError,UnicodeError) as error:raise CandidateFailure('Unsafe or missing candidate module') from error
    finally:os.close(fd)


def sources(workspace,key):
    values={};root=Path(workspace).resolve(strict=True)
    for prefix in PREFIXES[key]:
        directory=root/prefix
        if directory.is_symlink() or not directory.is_dir():raise CandidateFailure('Candidate module directory is missing or unsafe')
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():raise CandidateFailure('Candidate modules cannot include symlinks')
            if path.is_file() and path.suffix=='.mjs':
                name=path.relative_to(root).as_posix();values[name]=safe_read(root,name)
                if len(values)>64 or sum(len(x.encode()) for x in values.values())>524288:raise CandidateFailure('Candidate modules exceed limits')
    if not set(ENTRIES[key])<=set(values):raise CandidateFailure('Required candidate module is missing')
    return values


def compare(item,observed):
    if not isinstance(observed,dict) or set(observed)!={'value','after'}:return False
    after=copy.deepcopy(item['args'])
    if item.get('special')=='history-nan':after[0][0]=None
    if item.get('special')=='current-infinity':after[1]=None
    if observed['after']!=after:return False
    result=observed['value']
    if item['kind']=='decision':
        field=item['field']
        return (isinstance(result,dict) and set(result)=={field,'violations'} and type(result[field]) is bool and result[field] is item['expected'] and isinstance(result['violations'],list) and all(isinstance(x,str) and 0<len(x)<=2000 for x in result['violations']) and (len(result['violations'])==0)==result[field])
    if not isinstance(result,dict) or set(result)!={'band','mean','stddev','z'}:return False
    expected=item['expected']
    if type(result['band']) is not type(expected['band']) or result['band']!=expected['band']:return False
    for field in ('mean','stddev','z'):
        want=expected[field];got=result[field]
        if want is None:
            if got is not None:return False
        elif type(got) not in (int,float) or not math.isfinite(got) or not math.isclose(got,want,rel_tol=1e-9,abs_tol=1e-9):return False
    return True


def assess(workspace,key,image):
    if key not in ENTRIES:raise ValueError('Unsupported artifact exercise')
    if not isinstance(image,str) or not IMAGE.fullmatch(image):raise Unavailable('A digest-pinned worker image is required')
    try:available=subprocess.run(['docker','image','inspect',image],env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    except (OSError,subprocess.TimeoutExpired) as error:raise Unavailable('Docker is unavailable') from error
    if available.returncode:raise Unavailable('Pinned worker image must be preloaded')
    matrix=cases(key)
    try:
        module_sources=sources(workspace,key)
        with tempfile.TemporaryDirectory(prefix='tensorlearn-artifact-check-') as folder:
            root=Path(folder);candidate=root/'candidate';candidate.mkdir();trusted=root/'trusted';trusted.mkdir()
            (trusted/'probe.mjs').write_text(HARNESS)
            (trusted/'modules.json').write_text(json.dumps(module_sources))
            (trusted/'inputs.json').write_text(json.dumps([{k:v for k,v in item.items() if k in ('module','export','args','special')} for item in matrix],allow_nan=False))
            code,out,_=worker(candidate,trusted,image,['--experimental-vm-modules','/trusted/probe.mjs'])
            observed=decode_results(out) if code==0 else None
            valid=isinstance(observed,list) and len(observed)==len(matrix)
            return [{'id':item['id'],'passed':compare(item,observed[i]) if valid else False} for i,item in enumerate(matrix)]
    except CandidateFailure:return [{'id':item['id'],'passed':False} for item in matrix]
