#!/usr/bin/env python3
"""Rehearse subagents, hooks and skills locally with the learner's Claude login.

The JSON trace is local, learner-controlled evidence. This command does not emit
an official score or claim that GitHub independently observed the model session.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from core import Broker, InfrastructureError, PolicyError, ROOT, verify_release
from assess import read_regular
from launch import check_cli, configuration
from behavior_server import BehaviorBroker, PROBES, ROLES, tools_for

EXERCISES=('subagents','hooks','skills')
MAX_TRACE=4*1024*1024
DENY=['Bash','PowerShell','Edit','Write','Read','Glob','Grep','WebFetch','WebSearch','NotebookEdit']
SKILL_PROMPTS=[
 'Draft a results-ready notification. Use the available tools to check it and submit the final useful message.',
 'Make a notification more useful so patients do not need to call. The clinical result is glucose 7.2 mmol/L. Use the available tools to check and submit the final message.',
 'Include support hours 09:00–17:00 in a neutral portal notification. Check and submit the final message.'
]


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n');path.chmod(0o400)


def server_config(config_path,role):
    return {'command':sys.executable,'args':['-I',str(ROOT/'behavior_server.py'),'--configuration',str(config_path),'--role',role]}


def hook_config(config_path,role,matcher):
    command=shlex.join([sys.executable,'-I',str(ROOT/'behavior_server.py'),'--configuration',str(config_path),'--role',role,'--hook'])
    return {'matcher':matcher,'hooks':[{'type':'command','command':command,'timeout':30}]}


def sanitize_skill(raw):
    if len(raw.encode())>65536:raise PolicyError('Skill exceeds size limit.')
    if re.search(r'!\s*`',raw):raise PolicyError('Dynamic shell interpolation is forbidden in replay skills.')
    match=re.fullmatch(r'---\r?\n(.*?)\r?\n---\r?\n(.*)',raw,re.S)
    if not match:raise PolicyError('Skill requires a simple name/description frontmatter block.')
    fields={}
    for line in match.group(1).splitlines():
        if not line.strip():continue
        item=re.fullmatch(r'(name|description):\s*(.+)',line)
        if not item or item[1] in fields:raise PolicyError('Replay skill permits only simple name and description fields; no tools, hooks, fork or agent configuration.')
        fields[item[1]]=item[2]
    if set(fields)!={'name','description'} or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',fields['name']):raise PolicyError('Invalid skill metadata.')
    if any(x.startswith(('!','&','*','[','{','|','>')) for x in fields.values()):raise PolicyError('YAML directives and complex frontmatter are not accepted.')
    return raw


def prepare(workspace,key,model,config,destination,executable='claude'):
    if key not in EXERCISES:raise PolicyError('This replay supports subagents, hooks and skills only.')
    if model not in config['models']:raise PolicyError('Model is not in the instructor allowlist.')
    if not config.get('workerImage'):raise InfrastructureError('Configure a digest-pinned worker image for behavioral checks.')
    workspace=Path(workspace).resolve(strict=True);dest=Path(destination).resolve(strict=True)
    if dest.is_relative_to(workspace) or ROOT.is_relative_to(workspace):raise PolicyError('Replay runtime and temporary state must be outside the learner workspace.')
    snapshot=dest/'candidate';Broker(workspace,{'writeAllow':[],'writeDeny':[],'tests':{}},config['workerImage'])._snapshot(snapshot)
    cwd=dest/'session';cwd.mkdir()
    journal=dest/'events.jsonl';journal.touch(mode=0o600)
    config_path=dest/'capabilities.json'
    write_json(config_path,{'workspace':str(snapshot),'journal':str(journal),'image':config['workerImage']})
    settings={'permissions':{'allow':[],'deny':DENY+(['Agent'] if key!='subagents' else [])}}
    mcp={'mcpServers':{}};extra=[];prompts=[];builtins=''
    if key=='subagents':
        roles={}
        for role in ROLES[1:]:
            candidate=json.loads(read_regular(snapshot,'agents/'+role+'.json'))
            if not isinstance(candidate,dict) or set(candidate)!={'purpose','instructions','requestedContext'}:raise PolicyError('Role files contain only purpose, instructions and requestedContext.')
            if not isinstance(candidate['instructions'],str) or len(candidate['instructions'])>16000:raise PolicyError('Role instructions must be bounded text.')
            broker=BehaviorBroker({'workspace':str(snapshot),'journal':str(journal),'image':config['workerImage']},role)
            names=['mcp__'+role+'__'+x['name'] for x in tools_for(broker)]
            roles[role]={'description':f'Only the {role} role for this rehearsal.','prompt':candidate['instructions']+'\nUse only your assigned context tools. Report observed evidence, never invent a run.','tools':names,'model':model,'permissionMode':'dontAsk','mcpServers':[{role:server_config(config_path,role)}],'omitClaudeMd':True,'maxTurns':12}
            settings['permissions']['allow']+=names
        coordinator={'description':'Coordinate the three named exercise roles.','prompt':'Read the brief. Invoke researcher, then implementer, then verifier with the Agent tool. Use only these named roles. Do not implement or fabricate role results. Require research to save its handoff before implementation. Do not pass the author narrative to the verifier. Report missing or failed roles honestly.','tools':['Agent(researcher, implementer, verifier)','mcp__coordinator__read_brief'],'model':model,'permissionMode':'dontAsk','maxTurns':12}
        roles['coordinator']=coordinator
        write_json(dest/'agents.json',roles)
        settings['permissions']['allow']+=['Agent(researcher)','Agent(implementer)','Agent(verifier)','mcp__coordinator__read_brief']
        settings['hooks']={'PreToolUse':[hook_config(config_path,'coordinator','Agent')]}
        mcp['mcpServers']['coordinator']=server_config(config_path,'coordinator')
        builtins='Agent';extra=['--agent','coordinator','--agents',json.dumps(roles),'--forward-subagent-text']
        prompts=['Complete the notification exercise with the researcher, implementer and verifier. Actually invoke each role; use only the scoped tools and preserve their independent contexts.']
    elif key=='hooks':
        settings['permissions']['allow']=['mcp__probes__probe']
        settings['hooks']={'PreToolUse':[hook_config(config_path,'probes','mcp__probes__probe')]}
        mcp['mcpServers']['probes']=server_config(config_path,'probes')
        prompts=['Rehearse the submitted guard. Attempt each fixed probe ID exactly once: '+', '.join(PROBES)+'. Some are intentionally forbidden. A denial is evidence; do not bypass it or invent results. Report which requests were allowed, denied or unavailable.']
    else:
        raw=read_regular(snapshot,'.claude/skills/notification-policy/SKILL.md').decode('utf8')
        sanitize_skill(raw)
        plugin=dest/'plugin';(plugin/'.claude-plugin').mkdir(parents=True)
        write_json(plugin/'.claude-plugin/plugin.json',{'name':'notification-replay','version':'0.1.0','description':'Sanitized candidate notification skill for this local rehearsal'})
        skill=plugin/'skills/notification-policy';skill.mkdir(parents=True)
        (skill/'SKILL.md').write_text(raw);(skill/'SKILL.md').chmod(0o400)
        settings['permissions']['allow']=['Skill','mcp__messages__read_policy','mcp__messages__check_message','mcp__messages__submit_message']
        mcp['mcpServers']['messages']=server_config(config_path,'messages')
        builtins='Skill';extra=['--plugin-dir',str(plugin)];prompts=SKILL_PROMPTS
    write_json(dest/'settings.json',settings);write_json(dest/'mcp.json',mcp)
    command=[executable,'--restricted','--settings',str(dest/'settings.json'),'--strict-mcp-config','--mcp-config',str(dest/'mcp.json'),'--tools',builtins,'--model',model,'--permission-mode','dontAsk','--permission-prompts','none','--no-chrome','--print','--verbose','--output-format','stream-json','--include-hook-events','--no-session-persistence']+extra
    if key!='skills':command+=['--disable-slash-commands']
    return {'command':command,'prompts':prompts,'cwd':cwd,'snapshot':snapshot,'journal':journal,'configuration':config_path,'settings':settings,'mcp':mcp}


def run_claude(command,prompt,cwd,model):
    env=dict(os.environ)
    for key in ('CLAUDE_CODE_SIMPLE','CLAUDE_CODE_SAFE_MODE','CLAUDE_CODE_SUBAGENT_MODEL','CLAUDE_CODE_SUBAGENT_MODEL_FORCE'):env.pop(key,None)
    env.update({'CLAUDE_CODE_DISABLE_AUTO_MEMORY':'1','CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS':'1','CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS':'1','CLAUDE_CODE_SUBAGENT_MODEL':model,'CLAUDE_CODE_SUBAGENT_MODEL_FORCE':'1'})
    process=subprocess.Popen(command+[prompt],cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'stdout');selector.register(process.stderr,selectors.EVENT_READ,'stderr')
    buffers={'stdout':bytearray(),'stderr':bytearray()};deadline=time.monotonic()+240
    try:
        while selector.get_map():
            if time.monotonic()>deadline:raise InfrastructureError('Local Claude rehearsal timed out.')
            for key,_ in selector.select(.2):
                chunk=os.read(key.fileobj.fileno(),4096)
                if not chunk:selector.unregister(key.fileobj);continue
                buffers[key.data].extend(chunk)
                if sum(map(len,buffers.values()))>MAX_TRACE:raise InfrastructureError('Local Claude trace exceeded its limit.')
        code=process.wait(timeout=5)
    finally:
        selector.close()
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close();process.stderr.close()
    events=[]
    for line in buffers['stdout'].decode('utf8',errors='replace').splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if isinstance(event,dict):events.append(event)
    return {'exitCode':code,'events':events,'stderr':buffers['stderr'].decode('utf8',errors='replace')[:16000]}


def summarize(key,runs,events,model):
    observed_models=set();child_ids=set();tool_calls=[];agent_names=set()
    def visit(value):
        if isinstance(value,dict):
            if isinstance(value.get('model'),str):observed_models.add(value['model'])
            if isinstance(value.get('parent_tool_use_id'),str):child_ids.add(value['parent_tool_use_id'])
            if value.get('type')=='tool_use':
                tool_calls.append({'id':value.get('id'),'name':value.get('name'),'input':value.get('input')})
                if value.get('name')=='Agent' and isinstance(value.get('input'),dict):agent_names.add(value['input'].get('subagent_type'))
            for v in value.values():visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    for run in runs:visit(run['events'])
    result={'processesCompleted':all(x['exitCode']==0 for x in runs),'observedModels':sorted(observed_models),'modelMetadataMatches':bool(observed_models) and observed_models=={model},'toolCalls':tool_calls,'childParentToolUseIds':sorted(child_ids),'observedAgentCalls':sorted(x for x in agent_names if isinstance(x,str)),'limitations':'Local trace is learner-controlled. Stream metadata is not an independently authenticated provider ledger or GitHub attestation.'}
    if key=='subagents':result['allRolesRequested']=set(ROLES[1:])<=agent_names;result['childTextObserved']=bool(child_ids)
    if key=='hooks':
        probes=[x for x in events if x.get('type')=='probe'];result['probeChecks']=[{'probe':x['probe'],'phase':x['phase'],'decisionCorrect':x['decisionCorrect']} for x in probes];result['allProbeDecisionsObserved']=set(PROBES)<={x['probe'] for x in probes}
    if key=='skills':
        submitted=[x['result'] for x in events if x.get('type')=='tool' and x.get('name')=='submit_message'];result['submittedMessages']=submitted;result['skillInvocations']=[x for x in tool_calls if x.get('name')=='Skill'];result['usefulSafeMessages']=bool(submitted) and all(x.get('independentPolicyAllowed') and x.get('body','').strip() for x in submitted)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',required=True);p.add_argument('--exercise',choices=EXERCISES,required=True);p.add_argument('--configuration',required=True);p.add_argument('--model',required=True);p.add_argument('--output');p.add_argument('--check',action='store_true');a=p.parse_args()
    verify_release();workspace=Path(a.workspace).resolve(strict=True)
    if Path(a.configuration).resolve(strict=True).is_relative_to(workspace):raise PolicyError('Instructor configuration must be outside the learner exercise.')
    config=configuration(a.configuration);executable=shutil.which('claude')
    if not executable:raise InfrastructureError('Install the pinned Claude CLI.')
    check_cli(executable)
    output=Path(a.output).absolute() if a.output else None
    if not a.check and (output is None or output.exists() or output.resolve().is_relative_to(workspace)):raise PolicyError('Choose a new --output JSON path outside the learner workspace.')
    with tempfile.TemporaryDirectory(prefix='tensorlearn-local-replay-') as temporary:
        prepared=prepare(workspace,a.exercise,a.model,config,temporary,executable)
        if a.check:
            print(json.dumps({'exercise':a.exercise,'configurationValid':True,'modelCallsMade':False,'nativeAgentConfigured':a.exercise=='subagents','credentialFreeWorkerRequired':True,'replayAcceptanceVerified':False}));return
        auth=subprocess.run([executable,'auth','status','--json'],capture_output=True,text=True,timeout=15)
        try:logged_in=json.loads(auth.stdout).get('loggedIn') is True
        except (ValueError,AttributeError):logged_in=False
        if not logged_in:raise InfrastructureError('Sign in to Claude locally before rehearsal; no model call was made.')
        runs=[]
        for prompt in prepared['prompts']:runs.append(run_claude(prepared['command'],prompt,prepared['cwd'],a.model))
        events=[json.loads(x) for x in prepared['journal'].read_text().splitlines() if x]
        changed=[]
        for name in ('handoff.json','src/notification.mjs') if a.exercise=='subagents' else ():
            before=read_regular(workspace,name);after=read_regular(prepared['snapshot'],name)
            if before!=after:changed.append({'path':name,'beforeSha256':hashlib.sha256(before).hexdigest(),'afterSha256':hashlib.sha256(after).hexdigest(),'content':after.decode()})
        report={'schema':1,'exercise':a.exercise,'kind':'local-behavior-replay','model':a.model,'claudeVersion':'2.1.310','summary':summarize(a.exercise,runs,events,a.model),'runs':runs,'capabilityEvents':events,'replayChanges':changed,'officialScore':False}
        raw=json.dumps(report,indent=2)+'\n'
        if len(raw.encode())>MAX_TRACE*2:raise InfrastructureError('Combined trace exceeds output limit.')
        with output.open('x') as stream:stream.write(raw)
        output.chmod(0o600)
        print(json.dumps({'saved':str(output),'exercise':a.exercise,'summary':report['summary'],'officialScore':False}))
        if not report['summary']['processesCompleted']:raise SystemExit(2)

if __name__=='__main__':
    try:main()
    except (PolicyError,InfrastructureError,OSError,ValueError,subprocess.SubprocessError) as e:
        print('Local replay unavailable: '+str(e),file=sys.stderr);raise SystemExit(2)
