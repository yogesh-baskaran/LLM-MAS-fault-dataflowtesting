"""One process per non-payment suite; no cross-agent Python module collisions."""
from __future__ import annotations
import argparse
import importlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
import time

from jsonschema import validate
from agents.shared import Runtime,item_schema,checkpoint,check
from dataflow.common import now,redact,write_json,sha256
from dataflow.coverage import build_coverage
from dataflow.llm import Ollama
from dataflow.report import export_html
from dataflow.runner import ANALYSIS_SCHEMA


ACTION=item_schema({'tool':{'enum':['execute','inspect','finish']},'reason':{'type':'string','maxLength':300}})


def run(args):
    if not os.getenv('SLURM_JOB_ID') or socket.gethostname().startswith('speed-submit'):
        raise RuntimeError('Agent experiments require a compute-node SLURM allocation')
    os.environ.update(FAULT_MODE='NONE',BOUNDARY_EVENTS_ENABLED='0',PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('BOUNDARY_EVENTS_FILE',None)
    args.repo=args.repo.resolve();args.output=args.output.resolve();args.runtime=args.runtime.resolve();args.active_test=args.active_test.resolve();args.output.mkdir(parents=True,exist_ok=False)
    # Existing token_log.txt writes are redirected by the process working directory.
    os.chdir(args.output)
    package='agents.'+args.agent
    definition=importlib.import_module(package+'.cases')
    scope=json.loads((Path(__file__).parent.parent/'agents'/args.agent/'scope.json').read_text())
    runtime=Runtime(args,scope);adapter=None;tests=[];generated=[];events=[]
    metadata={'run_id':args.output.parent.name,'agent':args.agent,'execution':'SPEED_SLURM','slurm_job_id':os.getenv('SLURM_JOB_ID'),
              'hostname':socket.gethostname(),'python_version':platform.python_version(),'model':args.model,'fault_mode':'NONE',
              'status':'running','started_at':now(),'database':'isolated_job_instance','adaptations':['One worker process per suite','Read-only original source; in-memory or test-copy instrumentation','LLM-controlled test tool dispatcher'],
              'excluded':scope['exclusions']}
    coverage=build_coverage([],[]);sources={};fingerprints={};llm=Ollama(args.ollama_url,args.model,args.llm_timeout,args.context)
    def snapshot():
        report={'metadata':metadata,'tests':tests,'coverage':coverage,'sources':sources,'summary':{s:sum(t['status']==s for t in tests) for s in ['pass','fail','error']}}
        write_json(args.output/'generated_tests.json',generated);write_json(args.output/'execution_traces.json',tests)
        write_json(args.output/'coverage.json',coverage);write_json(args.output/'llm_analysis.json',[{'id':t['id'],**t['llm_analysis']} for t in tests]);write_json(args.output/'llm_calls.json',llm.calls)
        write_json(args.output/'report.json',report);export_html(report,args.output/'dashboard.html');return report
    snapshot()
    try:
        runtime.prepare();sources=runtime.sources
        fingerprints={name:sha256(args.repo/name) for name in sources};metadata['source_hashes']=fingerprints
        metadata['llm_provenance']=llm.provenance()
        adapter=importlib.import_module(package+'.adapter').Adapter(runtime)
        coverage=build_coverage(runtime.catalogs,[],args.max_paths,args.max_node_visits)
        schema=item_schema({'tests':{'type':'array','minItems':1,'maxItems':20,'items':item_schema({'id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,40}$'},'title':{'type':'string','maxLength':150},'input':definition.SCHEMA,'targets':{'type':'array','items':{'type':'string'},'maxItems':20}})}})
        for round_no in range(args.rounds+1):
            if round_no==0:
                cases=definition.baseline_cases()
                for case in cases:case.update(origin='deterministic_baseline',targets=[])
            else:
                gaps=[{k:p[k] for k in ['id','variable','use_kind','scope','definition','use']} for p in coverage['uses'] if not p['tests']][:20]
                answer=llm.ask('generation:'+args.agent+':'+str(round_no),
                    'Generate up to '+str(args.generated_per_round)+' diverse tests for '+args.agent+'. No fault injection. Use the input schema and target uncovered relationships. '
                    'Coverage targets are intentions, not evidence.\n'+json.dumps({'uncovered':gaps,'previous_inputs':[t['case']['input'] for t in tests],
                    'baseline_examples':[c['input'] for c in definition.baseline_cases()],
                    'scope':scope,'source_excerpt':{k:v[:9000] for k,v in list(sources.items())[:2]}}),schema)
                cases=answer['tests'][:args.generated_per_round]
                valid_targets={u['id'] for u in coverage['uses']}
                for case in cases:
                    case['origin']='llm_generated'
                    case['invalid_target_ids']=[target for target in case['targets'] if target not in valid_targets]
            for index,case in enumerate(cases,1):
                validate(case['input'],definition.SCHEMA)
                case['id']=f'{args.agent}_r{round_no}_{index}_{case["id"]}'[:95]
            generated.append({'round':round_no,'tests':cases});snapshot()
            for case in cases:
                runtime.start_case(case['id']);start=time.monotonic();workflow=[];execution={};error=None;status='error'
                charged=inspected=finished=False
                try:
                    for _ in range(5):
                        action=llm.ask('workflow:'+case['id'],'Execute the '+args.agent+' test using execute exactly once, then inspect, then finish. '
                            'Input is immutable; execute invokes the original agent workflow.\n'+json.dumps({'case':case,'history':workflow}),ACTION)
                        step={'action':action};workflow.append(step)
                        if action['tool']=='execute' and not charged:
                            charged=True;execution=adapter.execute(case);step['observation']={'result':redact(execution.get('result')),'checks':execution.get('checks',[])}
                            if not any('node' in event for event in runtime.events(case['id'])):
                                raise RuntimeError('Instrumentation produced no execution events for this test')
                        elif action['tool']=='inspect' and charged and not inspected:
                            inspected=True;step['observation']={'checks':execution.get('checks',[]),'rpc_calls':runtime.rpc_calls,'service_evidence':runtime.evidence()}
                        elif action['tool']=='finish' and charged and inspected:finished=True;break
                        else:raise ValueError('Invalid LLM tool ordering: '+action['tool'])
                    if not finished:raise ValueError('LLM workflow step limit reached')
                    checks=execution.get('checks',[])
                    status='pass' if checks and all(c['valid'] for c in checks) else 'fail'
                except Exception as exc:error={'type':type(exc).__name__,'message':redact(str(exc))}
                cps=runtime.checkpoints
                if not cps:cps=[checkpoint('TASK_START',case['input'],[check('fixture_bound',True)])]
                last=bad=None
                for cp in cps:
                    if cp['validation']['valid'] is False:bad=cp['step'];break
                    if cp['validation']['valid'] is True:last=cp['step']
                evidence={'status':status,'checkpoints':cps,'last_known_well':last,'first_deviation':bad,'checks':execution.get('checks',[]),'error':error}
                try:
                    analysis=llm.ask('analysis:'+case['id'],'Analyze actual evidence only. Missing/incomplete execution is inconclusive. Identify checkpoint names and evidence.\n'+json.dumps(redact(evidence)),ANALYSIS_SCHEMA)
                    analysis={'status':'completed','result':analysis,'agrees_with_oracle':analysis['verdict']==('inconclusive' if status=='error' else status),'agrees_with_lkw':analysis['last_known_well']==last and analysis['first_deviation']==bad}
                except Exception as exc:analysis={'status':'error','error':redact(str(exc))}
                case_events=[e for e in runtime.events(case['id']) if 'node' in e];events.extend(case_events)
                coverage=build_coverage(runtime.catalogs,events,args.max_paths,args.max_node_visits)
                test={'id':case['id'],'agent':args.agent,'case':case,'status':status,'elapsed_ms':round(1000*(time.monotonic()-start),2),
                      'workflow':workflow,'checkpoints':cps,'last_known_well':last,'first_deviation':bad,'error':error,
                      'rpc_calls':runtime.rpc_calls,'service_evidence':runtime.evidence(),'execution':execution,'events':case_events,'llm_analysis':analysis,
                      'expected':{'checks':execution.get('checks',[])},'persistence':{'verified':None,'note':'See suite-specific state/order checks'},
                      'covered_nodes':sorted(n for n,hits in coverage['node_hits'].items() if case['id'] in hits)}
                tests.append(test);snapshot();print(args.agent+': '+case['id']+' '+status,flush=True)
        metadata['status']='completed'
    except Exception as exc:metadata.update(status='error',error={'type':type(exc).__name__,'message':redact(str(exc))})
    finally:
        if adapter and hasattr(adapter,'close'):adapter.close()
        runtime.close();metadata['finished_at']=now();metadata['source_unchanged']=all(sha256(args.repo/name)==value for name,value in fingerprints.items());snapshot()
    if metadata['status']!='completed' or any(t['status']=='error' or t['llm_analysis']['status']!='completed' for t in tests):return 2
    return 1 if any(t['status']=='fail' for t in tests) else 0


def parser():
    p=argparse.ArgumentParser();p.add_argument('--agent',choices=['currency','emailservice','recommendation','cart','checkout'],required=True)
    for name in ['repo','output','runtime','active-test']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--ollama-url',default='http://127.0.0.1:11444');p.add_argument('--model',default='qwen2.5-coder:14b');p.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27027')
    for name,default in [('currency-port',50063),('email-port',50064),('recommendation-port',50065),('cart-port',50062),('checkout-port',50066),('rounds',1),('generated-per-round',3),('max-paths',10000),('max-node-visits',3),('llm-timeout',900),('rpc-timeout',900),('context',8192)]:p.add_argument('--'+name,type=int,default=default)
    return p


if __name__=='__main__':sys.exit(run(parser().parse_args()))
