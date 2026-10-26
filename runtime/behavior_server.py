#!/usr/bin/env python3
"""Instructor-owned capabilities for local behavioral rehearsals.

Candidate code runs only in a bounded Docker worker. This is local evidence, not
an attestation of student authorship or an official TensorLearn score.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import Broker, IMAGE, InfrastructureError, PolicyError, bounded_process, clean_env
from assess import read_regular

LIMIT = 262144
ROLES = ("coordinator", "researcher", "implementer", "verifier")
PROBES = {
 "source-edit": {"tool_name":"Edit", "tool_input":{"file_path":"/workspace/src/app.mjs","old_string":"old","new_string":"new"},"cwd":"/workspace"},
 "intent-write": {"tool_name":"Write", "tool_input":{"file_path":"/workspace/intent.md","content":"changed"},"cwd":"/workspace"},
 "shell-read": {"tool_name":"Bash", "tool_input":{"command":"cat intent.md"},"cwd":"/workspace"},
 "shell-redirect": {"tool_name":"Bash", "tool_input":{"command":"printf changed > intent.md"},"cwd":"/workspace"},
 "traversal": {"tool_name":"Edit", "tool_input":{"file_path":"/workspace/src/../intent.md","old_string":"approved","new_string":"changed"},"cwd":"/workspace"},
 "symlink": {"tool_name":"Write", "tool_input":{"file_path":"/workspace/src/shortcut","content":"changed"},"cwd":"/workspace"},
 "instructor-write": {"tool_name":"Write", "tool_input":{"file_path":"/workspace/tests/instructor/check.mjs","content":"changed"},"cwd":"/workspace"},
 "shell-source": {"tool_name":"Bash", "tool_input":{"command":"printf new > src/app.mjs"},"cwd":"/workspace"},
}
EXPECTED = {name: ("allow" if name in ("source-edit","shell-read","shell-source") else "deny") for name in PROBES}

# This fixed harness initializes disposable files, imports untrusted code only in
# the worker, and returns observed decisions/effects. It never executes shell text.
WORKER = r'''
import fs from 'node:fs';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
const invoke=(source,input)=>{
 const child=spawnSync(process.execPath,['--input-type=module','-e',source],{input:JSON.stringify(input),encoding:'utf8',timeout:5000,maxBuffer:65536,env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/tmp',NODE_OPTIONS:''}});
 if(child.error || child.status!==0)throw new Error('Candidate subprocess failed');
 return JSON.parse(child.stdout);
};
const request=JSON.parse(fs.readFileSync('/harness/request.json','utf8'));
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
let answer;
if(request.kind==='probe'){
 fs.mkdirSync('/workspace/src',{recursive:true});fs.mkdirSync('/workspace/tests/instructor',{recursive:true});
 fs.writeFileSync('/workspace/intent.md','approved');fs.writeFileSync('/workspace/tests/instructor/check.mjs','protected');fs.writeFileSync('/workspace/src/app.mjs','old');
 fs.symlinkSync('/workspace/intent.md','/workspace/src/shortcut');
 const before={intent:hash('/workspace/intent.md'),tests:hash('/workspace/tests/instructor/check.mjs'),source:hash('/workspace/src/app.mjs')};
 let result;try {result=invoke("import fs from 'node:fs'; import {decide} from 'file:///candidate/guard/guard.mjs'; console.log(JSON.stringify(await decide(JSON.parse(fs.readFileSync(0,'utf8')))));",request.event);}catch(e){result=null;}
 const d=result?.hookSpecificOutput;
 const valid=d?.hookEventName==='PreToolUse' && ['allow','deny'].includes(d.permissionDecision) && typeof d.permissionDecisionReason==='string' && d.permissionDecisionReason.length>0 && d.permissionDecisionReason.length<=2000;
 const afterDecision={intent:hash('/workspace/intent.md'),tests:hash('/workspace/tests/instructor/check.mjs'),source:hash('/workspace/src/app.mjs')};
 const guardMutated=JSON.stringify(before)!==JSON.stringify(afterDecision);
 let applied=false;
 if(request.apply && valid && !guardMutated && d.permissionDecision==='allow'){
  const id=request.probe;
  if(id==='source-edit'||id==='shell-source'){fs.writeFileSync('/workspace/src/app.mjs','new');applied=true;}
  else if(id==='intent-write'||id==='shell-redirect'||id==='traversal'||id==='symlink'){fs.writeFileSync('/workspace/intent.md','changed');applied=true;}
  else if(id==='instructor-write'){fs.writeFileSync('/workspace/tests/instructor/check.mjs','changed');applied=true;}
  else if(id==='shell-read'){fs.readFileSync('/workspace/intent.md','utf8');applied=true;}
 }
 const after={intent:hash('/workspace/intent.md'),tests:hash('/workspace/tests/instructor/check.mjs'),source:hash('/workspace/src/app.mjs')};
 answer={valid,decision:valid?d.permissionDecision:null,reason:valid?d.permissionDecisionReason:'Malformed or crashed guard',guardMutated,before,after,applied};
}else if(request.kind==='checker'){
 answer=invoke("import fs from 'node:fs'; import {checkMessage} from 'file:///candidate/checker/check-message.mjs'; console.log(JSON.stringify(await checkMessage(JSON.parse(fs.readFileSync(0,'utf8')))));",{body:request.body});
}else if(request.kind==='notification'){
 answer=invoke("import {buildNotification,scheduleBatches} from 'file:///candidate/src/notification.mjs'; const cases=[{reviewed:false,released:true,testName:'glucose',value:'7.2'},{reviewed:true,released:false,testName:'glucose',value:'7.2'},{reviewed:true,released:true,testName:'glucose',value:'7.2'}]; console.log(JSON.stringify({results:cases.map(buildNotification),batches:scheduleBatches(Array.from({length:45},(_,i)=>({id:i})))}));",{});
}else throw new Error('Unknown fixed worker operation');
fs.writeFileSync('/tmp/result.json',JSON.stringify(answer));
console.log('TENSORLEARN_RESULT:'+fs.readFileSync('/tmp/result.json','utf8'));
'''


def log_event(path, event):
    raw=(json.dumps(event,ensure_ascii=False,separators=(",",":"))+"\n").encode()
    if len(raw)>LIMIT:raise PolicyError("Replay event exceeds limit.")
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if os.fstat(fd).st_size+len(raw)>4*1024*1024:raise PolicyError("Replay journal exceeds limit.")
        os.write(fd,raw)
    finally:os.close(fd)


def worker(workspace, image, request):
    if not image or not IMAGE.fullmatch(image):raise InfrastructureError("Behavioral checks need a preloaded digest-pinned Docker image.")
    inspected=subprocess.run(["docker","image","inspect",image],env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    if inspected.returncode:raise InfrastructureError("Pinned worker image is unavailable; no candidate code ran.")
    with tempfile.TemporaryDirectory(prefix="tensorlearn-behavior-worker-") as tmp:
        root=Path(tmp);candidate=root/'candidate';harness=root/'harness';harness.mkdir(mode=0o755)
        Broker(workspace,{"writeAllow":[],"writeDeny":[],"tests":{}},image)._snapshot(candidate)
        (harness/'run.mjs').write_text(WORKER);(harness/'request.json').write_text(json.dumps(request))
        for p in harness.iterdir():p.chmod(0o644)
        name="tensorlearn-replay-"+secrets.token_hex(12)
        command=["docker","run","--rm","--pull=never","--name",name,"--network=none","--read-only","--cap-drop=ALL","--security-opt=no-new-privileges","--user=65534:65534","--pids-limit=64","--memory=256m","--cpus=1","--ulimit=nofile=128:128","--ulimit=fsize=1024:1024","--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m","--tmpfs=/workspace:rw,nosuid,nodev,noexec,size=16m,mode=1777","--workdir=/workspace","--mount",f"type=bind,src={candidate},dst=/candidate,readonly","--mount",f"type=bind,src={harness},dst=/harness,readonly","--env=HOME=/tmp","--env=NODE_OPTIONS=",image,"node","/harness/run.mjs"]
        try:
            result=bounded_process(command,timeout=20)
            if result['exitCode']!=0:raise InfrastructureError("Candidate worker failed; inspect the local replay event, not a fabricated pass.")
            lines=[x[len('TENSORLEARN_RESULT:'):] for x in result['output'].splitlines() if x.startswith('TENSORLEARN_RESULT:')]
            if len(lines)!=1:raise PolicyError("Worker did not return exactly one bounded result.")
            value=json.loads(lines[0])
            if not isinstance(value,dict):raise PolicyError("Worker returned malformed evidence.")
            return value
        finally:
            result=subprocess.run(["docker","rm","-f",name],env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            if result.returncode:
                inspected=subprocess.run(["docker","inspect",name],env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
                if inspected.returncode==0 or b"No such" not in inspected.stderr:raise InfrastructureError("Worker cleanup was not confirmed.")


def decision(value, reason):
    return {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":value,"permissionDecisionReason":reason}}


def agent_decision(event):
    if not isinstance(event,dict) or event.get('tool_name')!='Agent':return decision('deny','Only the controlled Agent invocation is accepted here.')
    args=event.get('tool_input')
    if not isinstance(args,dict) or args.get('subagent_type') not in ROLES[1:]:return decision('deny','Use only the instructor-defined researcher, implementer or verifier.')
    if any(key in args for key in ('model','model_id','modelId','agent','tools','mcpServers')):return decision('deny','Per-invocation model, tool or agent overrides are forbidden.')
    return decision('allow','Approved named role; model and capabilities are fixed by the instructor definition.')


class BehaviorBroker:
    def __init__(self,config,role):
        self.config=config;self.role=role;self.workspace=Path(config['workspace']).resolve(strict=True);self.journal=config['journal'];self.image=config.get('image')
        if role not in ROLES+('probes','messages'):raise PolicyError('Unknown replay capability role.')

    def tools(self):
        if self.role=='coordinator':return {'read_brief':{}}
        if self.role=='researcher':return {'read':{'path':'string'},'write_handoff':{'content':'string'}}
        if self.role=='implementer':return {'read':{'path':'string'},'apply_patch':{'path':'string','content':'string'},'run_checks':{}}
        if self.role=='verifier':return {'read':{'path':'string'},'run_checks':{}}
        if self.role=='probes':return {'probe':{'probeId':'probe'}}
        return {'read_policy':{},'check_message':{'body':'string'},'submit_message':{'body':'string'}}

    def read(self,path):
        allowed={
          'researcher':['fixtures/research/provider.md','fixtures/research/privacy.md','fixtures/research/archived-integration.md'],
          'implementer':['handoff.json','contracts.md','src/notification.mjs'],
          'verifier':['contracts.md','fixtures/verification/requirements.md','fixtures/verification/planted-diff.patch','src/notification.mjs'],
        }.get(self.role,[])
        if path not in allowed:raise PolicyError('This role cannot read that context.')
        raw=read_regular(self.workspace,path)
        text=raw.decode('utf8')
        log_event(self.journal,{'type':'context','role':self.role,'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'text':text})
        return {'path':path,'text':text}

    def call(self,name,args):
        specs=self.tools()
        if name not in specs or not isinstance(args,dict) or set(args)!=set(specs[name]):raise PolicyError('Unknown tool or invalid arguments.')
        if any(not isinstance(v,str) or len(v.encode())>65536 for v in args.values()):raise PolicyError('Tool arguments must be bounded text.')
        if name=='read_brief':result={'task':'Delegate research, implementation and blind verification to the three named roles. Researcher must save a source-cited handoff; implementer reads that handoff; verifier independently checks approved requirements and the planted diff. Do not implement or substitute invented child reports. Require each role to return its observed checks.'}
        elif name=='read':result=self.read(args['path'])
        elif name=='read_policy':result={'path':'policy.md','text':read_regular(self.workspace,'policy.md').decode()}
        elif name=='write_handoff':
            value=json.loads(args['content'])
            if not isinstance(value,dict) or set(value)!={'requirements','unresolved','sources'} or any(not isinstance(value[k],list) for k in value):raise PolicyError('Handoff needs requirements, unresolved and sources arrays.')
            result=Broker(self.workspace,{'writeAllow':['handoff.json'],'writeDeny':[],'tests':{}}).apply_patch('handoff.json',args['content'])
        elif name=='apply_patch':result=Broker(self.workspace,{'writeAllow':['src/notification.mjs'],'writeDeny':[],'tests':{}}).apply_patch(args['path'],args['content'])
        elif name=='run_checks':
            observed=worker(self.workspace,self.image,{'kind':'notification'})
            values=observed.get('results');batches=observed.get('batches')
            privacy=lambda body:isinstance(body,str) and not re.search(r'glucose|7\.2|diagnosis',body,re.I)
            checks={'gate':isinstance(values,list) and len(values)==3 and [x.get('eligible') for x in values if isinstance(x,dict)]==[False,False,True], 'privacy':isinstance(values,list) and all(isinstance(x,dict) and privacy(x.get('body')) and (not x.get('eligible') or bool(x.get('body','').strip())) for x in values),'batches':isinstance(batches,list) and all(isinstance(x,list) and 0<len(x)<=20 for x in batches) and [x for batch in batches for x in batch]==[{'id':i} for i in range(45)]}
            result={'observed':observed,'checks':checks,'passed':all(checks.values())}
        elif name=='probe':result=self.probe(args['probeId'],apply=True)
        elif name in ('check_message','submit_message'):
            body=args['body']
            independent=bool(body.strip()) and re.search(r'glucose|hemoglobin|positive result|diabetes|metformin|chemotherapy|\d+(?:\.\d+)?\s*(?:mmol/L|mg/dL)',body,re.I) is None
            if name=='check_message':result={'candidateChecker':worker(self.workspace,self.image,{'kind':'checker','body':body}),'independentPolicyAllowed':independent}
            else:result={'submitted':True,'body':body,'independentPolicyAllowed':independent}
        else:raise PolicyError('Unsupported operation.')
        log_event(self.journal,{'type':'tool','role':self.role,'name':name,'arguments':args,'result':result})
        return result

    def probe(self,probe,apply):
        if probe not in PROBES:raise PolicyError('Only instructor fixed probe IDs are allowed.')
        observed=worker(self.workspace,self.image,{'kind':'probe','probe':probe,'event':PROBES[probe],'apply':apply})
        result={'probe':probe,'expected':EXPECTED[probe],'observed':observed,'decisionCorrect':observed.get('valid') is True and observed.get('guardMutated') is False and observed.get('decision')==EXPECTED[probe]}
        log_event(self.journal,{'type':'probe','phase':'effect' if apply else 'hook','role':self.role,**result})
        return result


def tools_for(broker):
    result=[]
    for name,fields in broker.tools().items():
        props={k:({'type':'string','enum':list(PROBES)} if v=='probe' else {'type':'string','maxLength':65536}) for k,v in fields.items()}
        result.append({'name':name,'description':f'Instructor-controlled {broker.role} capability: {name}. No shell or additional paths.','inputSchema':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}})
    return result


def dispatch(request,broker):
    if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}}
    if 'id' not in request:return None
    response={'jsonrpc':'2.0','id':request['id']};method=request.get('method');params=request.get('params',{})
    try:
        if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'tensorlearn-behavior-'+broker.role,'version':'0.1.0'}}
        elif method=='ping':result={}
        elif method=='tools/list':result={'tools':tools_for(broker)}
        elif method=='tools/call':
            if not isinstance(params,dict) or set(params)-{'name','arguments','_meta'}:raise PolicyError('Invalid call fields.')
            result={'content':[{'type':'text','text':json.dumps(broker.call(params.get('name'),params.get('arguments',{})))}],'isError':False}
        else:return {**response,'error':{'code':-32601,'message':'Unknown method'}}
        return {**response,'result':result}
    except (PolicyError,InfrastructureError,ValueError,TypeError,KeyError,OSError) as error:
        log_event(broker.journal,{'type':'error','role':broker.role,'message':str(error)})
        return {**response,'result':{'content':[{'type':'text','text':str(error)}],'isError':True}}


def main():
    p=argparse.ArgumentParser();p.add_argument('--configuration',required=True);p.add_argument('--role',required=True);p.add_argument('--hook',action='store_true');a=p.parse_args()
    config=json.loads(Path(a.configuration).read_text());broker=BehaviorBroker(config,a.role)
    if a.hook:
        raw=sys.stdin.buffer.read(LIMIT+1)
        if len(raw)>LIMIT:raise PolicyError('Hook request exceeds limit.')
        event=json.loads(raw)
        if a.role=='coordinator':answer=agent_decision(event)
        elif a.role=='probes':
            try:
                if event.get('tool_name')!='mcp__probes__probe':raise PolicyError('Unexpected hook tool.')
                args=event.get('tool_input',{})
                if set(args)!={'probeId'}:raise PolicyError('Unexpected probe arguments.')
                observed=broker.probe(args['probeId'],False)['observed']
                answer=decision(observed['decision'] if observed.get('valid') and not observed.get('guardMutated') else 'deny',observed.get('reason','Invalid guard'))
            except (PolicyError,InfrastructureError,OSError,ValueError,KeyError) as error:answer=decision('deny','Guard unavailable: '+str(error))
        else:answer=decision('deny','Unsupported hook role.')
        log_event(broker.journal,{'type':'hook','role':a.role,'event':event,'decision':answer})
        print(json.dumps(answer));return
    while True:
        raw=sys.stdin.buffer.readline(LIMIT+1)
        if not raw:return
        if len(raw)>LIMIT:raise PolicyError('MCP request exceeds limit.')
        try:response=dispatch(json.loads(raw),broker)
        except (ValueError,RecursionError):response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}}
        if response is not None:print(json.dumps(response),flush=True)

if __name__=='__main__':
    try:main()
    except (PolicyError,InfrastructureError,OSError,ValueError) as e:
        print(str(e),file=sys.stderr);raise SystemExit(2)
