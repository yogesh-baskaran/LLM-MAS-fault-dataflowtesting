"""Sequential process-isolated suite coordinator with combined offline reporting."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid
from datetime import datetime,timezone

from dataflow.common import write_json,now
from dataflow.report import export_html

AGENTS=['payment','currency','emailservice','recommendation','cart','checkout']


def select_agents(value):
    agents=AGENTS[:] if value=='all' else [part.strip() for part in value.split(',') if part.strip()]
    if not agents or len(agents)!=len(set(agents)) or any(a not in AGENTS for a in agents):
        raise ValueError('Choose all or unique comma-separated agent names: '+','.join(AGENTS))
    return agents


def combine(reports,metadata):
    tests=[];catalogs=[];sources={};definitions=[];uses=[];paths=[];hits={};limitations=[]
    truncated=False;mismatches=[];outside=0
    for agent,report in reports.items():
        for test in report['tests']:test['agent']=agent
        tests.extend(report['tests']);cov=report['coverage'];catalogs+=cov['catalogs'];sources.update(report['sources'])
        for name,destination in [('definitions',definitions),('uses',uses),('du_paths',paths)]:
            for item in cov[name]:destination.append({**item,'id':agent+':'+item['id'],'agent':agent})
        for node,testids in cov['node_hits'].items():hits[node]=sorted(set(hits.get(node,[])+testids))
        truncated|=cov.get('path_enumeration_truncated',False);mismatches+=cov.get('observed_paths_missing_from_cfg',[]);outside+=cov.get('observed_du_paths_outside_bound',0)
        limitations.extend(cov.get('limitations',[]))
    def metric(items):
        covered=sum(bool(i['tests']) for i in items);total=len(items)
        return dict(covered=covered,total_candidates=total,candidate_percent=round(100*covered/total,2) if total else None,uncovered_candidates=total-covered)
    coverage={'catalogs':catalogs,'definitions':definitions,'uses':uses,'du_paths':paths,'node_hits':hits,
              'all_definitions':metric(definitions),'all_uses':metric(uses),'all_du_paths':metric(paths),
              'path_enumeration_truncated':truncated,'observed_paths_missing_from_cfg':mismatches,
              'observed_du_paths_outside_bound':outside,'complete_feasible_coverage_claim':False,
              'limitations':list(dict.fromkeys(limitations))}
    return {'metadata':metadata,'tests':tests,'coverage':coverage,'sources':sources,'agent_reports':reports,
            'summary':{s:sum(t['status']==s for t in tests) for s in ['pass','fail','error']}}


def main():
    p=argparse.ArgumentParser();p.add_argument('--agents',default='all')
    for name in ['root','repo','runtime']:p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();agents=select_agents(args.agents)
    if not os.getenv('SLURM_JOB_ID') or socket.gethostname().startswith('speed-submit'):raise RuntimeError('Use an existing compute allocation')
    args.root=args.root.resolve();args.repo=args.repo.resolve();args.runtime=args.runtime.resolve()
    out=args.root/'results'/('run_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]);out.mkdir(parents=True)
    reports={};statuses={};metadata={'run_id':out.name,'execution':'SPEED_SLURM','slurm_job_id':os.getenv('SLURM_JOB_ID'),
        'hostname':socket.gethostname(),'model':os.environ['DF_MODEL'],'fault_mode':'NONE','database':'isolated_job_instance',
        'agents':agents,'started_at':now(),'status':'running','adaptations':['Sequential suites; separate process per agent','Native Go/C# test copies; Python in-memory instrumentation'],
        'excluded':['Full-system DU-path coverage','Production deployment','Live SMTP delivery'],'max_node_visits':int(os.getenv('DF_MAX_NODE_VISITS','3'))}
    def snapshot():
        metadata['source_hashes']={name:value for report in reports.values() for name,value in report['metadata'].get('source_hashes',{}).items()}
        metadata['source_unchanged']=all(report['metadata'].get('source_unchanged') is True for report in reports.values()) if reports else None
        report=combine(reports,metadata);write_json(out/'report.json',report);write_json(out/'manifest.json',{**metadata,'suite_exit_codes':statuses});export_html(report,out/'dashboard.html')
    metadata['runtime_evidence']={name:(args.runtime/name).read_text(encoding='utf-8') for name in ['gpu.txt','gpu-details.txt','container-sha256.txt','python-environment.txt'] if (args.runtime/name).exists()}
    snapshot();print('Combined results: '+str(out),flush=True)
    for agent in agents:
        common=['--repo',str(args.repo),'--ollama-url','http://127.0.0.1:'+os.environ['DF_OLLAMA_PORT'],'--model',os.environ['DF_MODEL'],
                '--context',os.getenv('DF_CONTEXT','8192'),'--rounds',os.getenv('DF_ROUNDS','1'),'--generated-per-round',os.getenv('DF_GENERATED_PER_ROUND','3'),
                '--mongo-uri','mongodb://127.0.0.1:'+os.environ['DF_MONGO_PORT']]
        if agent=='payment':
            command=[sys.executable,'-B','-m','dataflow.runner',*common,'--output',str(out/'payment'),
                     '--service-catalog',str(args.runtime/'service/service-catalog.json'),'--service-events',str(args.runtime/'service-events.jsonl'),
                     '--payment-target','127.0.0.1:'+os.environ['DF_PAYMENT_PORT']]
        else:
            command=[sys.executable,'-B','-m','dataflow.agent_worker',*common,'--agent',agent,'--output',str(out/agent),
                     '--runtime',str(args.runtime),'--active-test',str(args.runtime/'active-test.txt'),
                     '--max-node-visits',os.getenv('DF_MAX_NODE_VISITS','3'),'--max-paths',os.getenv('DF_MAX_PATHS','10000')]
            for name in ['cart','currency','email','recommendation','checkout']:command+=['--'+name+'-port',os.environ['DF_'+name.upper()+'_PORT']]
        print('Starting suite '+agent,flush=True)
        env=os.environ.copy();env['PYTHONPATH']=str(args.root);env['PYTHONDONTWRITEBYTECODE']='1'
        with (out/(agent+'.log')).open('w',encoding='utf-8') as log:
            result=subprocess.run(command,cwd=args.root,env=env,stdout=log,stderr=subprocess.STDOUT)
        statuses[agent]=result.returncode
        reportfiles=list((out/agent).rglob('report.json')) if (out/agent).exists() else []
        if reportfiles:reports[agent]=json.loads(max(reportfiles,key=lambda p:p.stat().st_mtime).read_text())
        else:
            metadata.setdefault('suite_errors',{})[agent]='Worker exited before producing report; inspect '+agent+'.log'
        snapshot()
    metadata.update(status='completed' if all(code in {0,1} for code in statuses.values()) else 'error',finished_at=now());snapshot()
    print('Open '+str(out/'dashboard.html'),flush=True)
    return 2 if metadata['status']=='error' else 1 if any(statuses.values()) else 0


if __name__=='__main__':sys.exit(main())
