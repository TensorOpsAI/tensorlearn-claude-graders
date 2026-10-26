"""Deterministic validation of a learner-local AI report, not provider attestation.

The learner's Claude login runs the semantic judgment locally. GitHub checks the
frozen artifacts, source hashes, quoted evidence, rubric and arithmetic. It cannot
prove who ran a model, which model ran, or whether a quoted passage supports an
answer. These reports must never be described as independent CI model judgments.
"""
import fnmatch
import hashlib
import json
from pathlib import Path
import re

class Invalid(ValueError):
    pass

def strict_json(data):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value: raise ValueError('Duplicate JSON key')
            value[key]=item
        return value
    try:
        return json.loads(data,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite')))
    except (ValueError,UnicodeError,RecursionError) as error:
        raise Invalid('Invalid assessment JSON') from error

def matches(path,patterns):
    return any(fnmatch.fnmatchcase(path,p) for p in patterns)

def sources(workspace,rubric):
    selected={}
    patterns=rubric['sourceFiles']
    if not isinstance(patterns,list) or not patterns: raise ValueError('Missing trusted source list')
    for pattern in patterns:
        if pattern.startswith('/') or '..' in pattern.split('/') or '\\' in pattern:
            raise ValueError('Unsafe trusted source pattern')
        if not any(c in pattern for c in '*?[') and not (workspace/pattern).is_file():
            raise Invalid('An assessed source artifact is missing')
    for path in sorted(workspace.rglob('*')):
        name=path.relative_to(workspace).as_posix()
        if name=='assessment.json' or not matches(name,patterns): continue
        if path.is_symlink(): raise Invalid('Assessment sources cannot be symlinks')
        if not path.is_file(): continue
        raw=path.read_bytes()
        if len(raw)>65536: raise Invalid('Assessed artifact exceeds 64 KiB')
        try: text=raw.decode('utf-8')
        except UnicodeError as error: raise Invalid('Assessed artifact is not UTF-8') from error
        selected[name]={'hash':hashlib.sha256(raw).hexdigest(),'text':text}
    if not selected or len(selected)>100: raise Invalid('Invalid assessed artifact inventory')
    return selected

def assess(workspace,key,grader_sha,rubric_bytes):
    rubric=strict_json(rubric_bytes)
    if rubric.get('schema')!=1 or rubric.get('exercise')!=key: raise ValueError('Trusted rubric identity mismatch')
    criteria=rubric['criteria']
    if not isinstance(criteria,list) or not criteria or sum(c['points'] for c in criteria)!=100:
        raise ValueError('Trusted rubric must total 100 points')
    if any(type(c['points']) is not int or c['points']<1 or type(c['expected']) is not bool for c in criteria):
        raise ValueError('Invalid trusted rubric points/expected answers')
    ids=[c['id'] for c in criteria]
    if len(set(ids))!=len(ids): raise ValueError('Duplicate trusted criterion')
    path=workspace/'assessment.json'
    try:
        data=path.read_bytes()
        if path.is_symlink() or len(data)>65536: raise Invalid('Assessment exceeds limit or is a symlink')
        report=strict_json(data)
    except OSError as error: raise Invalid('Run the local assessment before submitting') from error
    fields={'schema','exercise','assessmentKind','graderRevision','rubricSha256','sourceHashes','model','criteria'}
    if not isinstance(report,dict) or set(report)!=fields:
        raise Invalid('Assessment fields do not match schema 1')
    if (type(report['schema']) is not int or report['schema']!=1 or report['exercise']!=key
        or report['assessmentKind']!='local-ai-report' or report['graderRevision']!=grader_sha
        or report['rubricSha256']!=hashlib.sha256(rubric_bytes).hexdigest()
        or not isinstance(report['model'],str) or not re.fullmatch(r'claude-[a-z]+-[0-9][a-z0-9-]*',report['model'])):
        raise Invalid('Assessment revision, rubric or identity is stale/invalid')
    source=sources(workspace,rubric)
    expected_hashes={name:item['hash'] for name,item in source.items()}
    if report['sourceHashes']!=expected_hashes: raise Invalid('Source files changed since local assessment; assess again')
    answers=report['criteria']
    if not isinstance(answers,list) or len(answers)!=len(criteria): raise Invalid('Incomplete criterion set')
    by_id={}
    for answer in answers:
        if (not isinstance(answer,dict) or set(answer)!={'id','answer','evidence'}
            or answer['id'] not in ids or answer['id'] in by_id or type(answer['answer']) is not bool
            or not isinstance(answer['evidence'],list) or len(answer['evidence'])>8):
            raise Invalid('Invalid or duplicate criterion answer')
        by_id[answer['id']]=answer
    checks=[];points=0;cap=100
    for criterion in criteria:
        answer=by_id[criterion['id']]
        evidence=answer['evidence']
        if criterion['evidence'].get('required',True) and not evidence:
            raise Invalid('Required criterion evidence is missing')
        for item in evidence:
            if not isinstance(item,dict) or set(item)!={'path','quote'}:
                raise Invalid('Invalid evidence shape')
            name=item['path'];quote=item['quote']
            if (not isinstance(name,str) or name not in source or not matches(name,criterion['evidence']['files'])
                or not isinstance(quote,str) or not quote.strip() or len(quote)>4000 or quote not in source[name]['text']):
                raise Invalid('Evidence is not an exact quote from an allowed frozen artifact')
        passed=answer['answer'] is criterion['expected']
        checks.append({'id':criterion['id'],'passed':passed})
        if passed: points+=criterion['points']
        elif 'criticalCap' in criterion:
            ceiling=criterion['criticalCap']
            if type(ceiling) is not int or not 0<=ceiling<=100: raise ValueError('Invalid trusted critical cap')
            cap=min(cap,ceiling)
    return checks,min(points,cap)
