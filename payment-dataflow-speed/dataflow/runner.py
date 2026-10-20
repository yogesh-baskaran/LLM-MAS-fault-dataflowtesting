from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import platform
import re
import socket
import sys
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .common import now, redact, sha256, write_json
from .coverage import Recorder, build_coverage, prepare_python
from .llm import Ollama

CASE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['id', 'title', 'card_profile', 'expiry', 'currency_code', 'units', 'nanos', 'targets'],
    'properties': {
        'id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,60}$'},
        'title': {'type': 'string', 'maxLength': 200},
        'card_profile': {'enum': ['visa', 'mastercard', 'amex', 'invalid', 'empty']},
        'expiry': {'enum': ['future', 'expired', 'current_month']},
        'currency_code': {'type': 'string', 'maxLength': 10},
        'units': {'type': 'integer', 'minimum': -1, 'maximum': 1000000},
        'nanos': {'type': 'integer', 'minimum': -1, 'maximum': 1000000000},
        'targets': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20},
    },
}
GENERATION_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['tests'],
                     'properties': {'tests': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': CASE_SCHEMA}}}
ACTION_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['tool', 'reason'],
                 'properties': {'tool': {'enum': ['charge_payment', 'inspect_transaction', 'finish']},
                                'reason': {'type': 'string', 'maxLength': 400}}}
ANALYSIS_SCHEMA = {'type': 'object', 'additionalProperties': False,
                   'required': ['verdict', 'last_known_well', 'first_deviation', 'explanation', 'evidence'],
                   'properties': {'verdict': {'enum': ['pass', 'fail', 'inconclusive']},
                                  'last_known_well': {'type': ['string', 'null']},
                                  'first_deviation': {'type': ['string', 'null']},
                                  'explanation': {'type': 'string', 'maxLength': 2000},
                                  'evidence': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20}}}

# Synthetic public test numbers; never consume user card data.
CARDS = {'visa': '4111111111111111', 'mastercard': '5555555555554444',
         'amex': '378282246310005', 'invalid': '1234567890123456', 'empty': ''}


def baseline_cases():
    examples = [('visa_success', 'visa', 'future', 'USD', 10, 990000000),
                ('mastercard_success', 'mastercard', 'future', 'CAD', 20, 0),
                ('invalid_card', 'invalid', 'future', 'USD', 10, 0),
                ('unsupported_amex', 'amex', 'future', 'USD', 10, 0),
                ('expired_card', 'visa', 'expired', 'USD', 10, 0),
                ('current_month', 'visa', 'current_month', 'USD', 1, 1),
                ('empty_card', 'empty', 'future', 'USD', 10, 0),
                ('zero_amount', 'visa', 'future', 'USD', 0, 0),
                ('negative_units', 'visa', 'future', 'USD', -1, 0),
                ('nanos_overflow', 'visa', 'future', 'USD', 10, 1000000000)]
    return [{'id': f'seed_{name}', 'title': name.replace('_', ' '), 'card_profile': card,
             'expiry': expiry, 'currency_code': currency, 'units': units, 'nanos': nanos,
             'targets': [], 'origin': 'deterministic_baseline'}
            for name, card, expiry, currency, units, nanos in examples]


def payload_for(case):
    current = datetime.now(timezone.utc)
    year = current.year + (2 if case['expiry'] == 'future' else -1 if case['expiry'] == 'expired' else 0)
    return {'query': 'Process payment for this test order',
            'currency_code': case['currency_code'], 'units': case['units'], 'nanos': case['nanos'],
            'credit_card_number': CARDS[case['card_profile']], 'credit_card_cvv': 123,
            'credit_card_expiration_year': year, 'credit_card_expiration_month': current.month}


def expectation(case):
    valid_card = case['card_profile'] in {'visa', 'mastercard'} and case['expiry'] != 'expired'
    valid_amount = (case['units'] >= 0 and 0 <= case['nanos'] < 1000000000
                    and bool(re.fullmatch('[A-Z]{3}', case['currency_code'])))
    return {'should_succeed': valid_card and valid_amount,
            'card_accepted': valid_card, 'amount_valid': valid_amount,
            'policy': 'VISA/MasterCard, nonexpired; units>=0; 0<=nanos<1e9; uppercase 3-letter currency; zero is allowed.'}


def load_instrumented(repo, recorder, prepared):
    """Execute AST-instrumented source in memory; existing files stay read-only."""
    sys.path.insert(0, str(repo / 'src' / 'paymentagent'))
    sys.path.insert(0, str(repo / 'src'))  # shared boundary helper, not a defective fallback
    importlib.import_module('app')
    modules = {}
    for name, code in prepared:
        module = types.ModuleType(name)
        module.__package__ = 'app'
        module.__file__ = str(repo / 'src' / 'paymentagent' / 'app' / (name.split('.')[-1]+'.py'))
        module._df_recorder = recorder
        sys.modules[name] = module
        exec(code, module.__dict__)
        modules[name] = module
    fi = importlib.import_module('app.fault_injection')
    fi.FAULT_MODE = 'NONE'
    original_record = fi.LKWCheckpoint.record
    def capture_checkpoint(checkpoint, step, data):
        original_record(checkpoint, step, data)
        recorder.checkpoints[recorder.test_id].append(checkpoint.checkpoints[-1])
    fi.LKWCheckpoint.record = capture_checkpoint
    return modules['app.agent'], modules['app.repository'], fi


class Bridge:
    def __init__(self, target):
        import grpc
        from app.clients import demo_pb2, demo_pb2_grpc
        from app.tools import CreditCardError
        self.grpc, self.pb, self.credit_error = grpc, demo_pb2, CreditCardError
        self.channel = grpc.insecure_channel(target)
        grpc.channel_ready_future(self.channel).result(timeout=30)
        self.stub = demo_pb2_grpc.PaymentServiceStub(self.channel)
        self.test_id = None
        self.calls = []

    def charge(self, request):
        start = time.monotonic()
        record = {'test_id': self.test_id, 'request': redact(request), 'transport': 'real_grpc'}
        message = self.pb.ChargeRequest(amount=self.pb.Money(**request['amount']),
                                        credit_card=self.pb.CreditCardInfo(**request['credit_card']))
        try:
            response = self.stub.Charge(message, timeout=30,
                                       metadata=[('x-dataflow-test-id', self.test_id)])
            record['transaction_id'] = response.transaction_id
            record['status'] = 'ok'
            return {'transaction_id': response.transaction_id}
        except self.grpc.RpcError as error:
            details = error.details() or ''
            record.update(status='rpc_error', code=error.code().name, details=redact(details))
            # Application rejection is distinct from connection/timeout failures. Original service
            # reports custom code 400, which gRPC may normalize to UNKNOWN.
            if any(marker in details for marker in ['Credit card info is invalid', 'Only VISA or MasterCard', 'expired on']):
                raise self.credit_error(details) from error
            raise
        finally:
            record['elapsed_ms'] = round(1000*(time.monotonic()-start), 2)
            self.calls.append(record)


def checkpoint_validation(result, payload, expected, persistence, rpc_calls):
    checkpoints = []
    successful_rpc = next((c for c in rpc_calls if c['status'] == 'ok'), None)
    for cp in result.get('lkw', []):
        step, data = cp['step'], cp['data']
        if step == 'TASK_START':
            valid = all(data.get(k) == payload[k] for k in ['currency_code', 'units', 'nanos'])
            reason = 'Checkpoint preserves test input'
        elif step == 'CARD_VALIDATED':
            valid = expected['card_accepted'] and not data.get('validation_bypassed')
            reason = 'PaymentService accepted a supported nonexpired card'
        elif step == 'CHARGE_DONE':
            valid = bool(successful_rpc) and data.get('transaction_id') == successful_rpc.get('transaction_id') and expected['amount_valid']
            valid = valid and data.get('units_charged') == payload['units'] and data.get('nanos_charged') == payload['nanos']
            reason = 'Returned transaction and amount satisfy the independent contract'
        elif step == 'SAVE_DONE':
            valid = data.get('saved') is True and persistence.get('verified') is True
            reason = 'Real MongoDB record matches transaction and amount'
        elif step == 'FINAL_ANSWER':
            valid = data.get('success') is expected['should_succeed']
            if data.get('success'):
                valid = valid and persistence.get('verified') is True
            reason = 'Final outcome matches independent input and persistence expectations'
        else:
            valid, reason = None, 'Checkpoint has no validator in this scope'
        checkpoints.append({**cp, 'validation': {'valid': valid, 'reason': reason}})
    last_well, first_bad = None, None
    for cp in checkpoints:
        if cp['validation']['valid'] is False:
            first_bad = cp['step']
            break
        if cp['validation']['valid'] is True:
            last_well = cp['step']
    return checkpoints, last_well, first_bad


async def verify_persistence(collection, result, payload, before_count):
    data = result.get('data', {})
    transaction_id = data.get('transaction_id')
    after_count = await collection.count_documents({})
    if not data.get('success'):
        return {'verified': after_count == before_count, 'count_before': before_count,
                'count_after': after_count, 'expected_insertions': 0}
    rows = await collection.find({'transaction_id': transaction_id}).to_list(length=2)
    expected = {'transaction_id': transaction_id, 'currency_code': payload['currency_code'],
                'units': payload['units'], 'nanos': payload['nanos'],
                'credit_card_last4': payload['credit_card_number'][-4:], 'status': 'success'}
    matching = len(rows) == 1 and all(rows[0].get(k) == v for k, v in expected.items())
    return {'verified': matching and after_count == before_count+1, 'matching_records': len(rows),
            'count_before': before_count, 'count_after': after_count, 'expected_insertions': 1,
            'record': ({k: str(v) if k in {'_id', 'created_at'} else v for k, v in rows[0].items()} if rows else None)}


async def execute_case(case, agent, collection, bridge, recorder, llm, max_steps=5):
    payload, expected = payload_for(case), expectation(case)
    recorder.test_id, bridge.test_id = case['id'], case['id']
    event_start, call_start = len(recorder.events), len(bridge.calls)
    before_count = await collection.count_documents({})
    start = time.monotonic()
    result, persistence, workflow, error, status = {}, {}, [], None, 'error'
    charged, inspected, finished = False, False, False
    try:
        for step in range(max_steps):
            action = llm.ask('workflow:'+case['id'],
                'Execute this payment test through the allowed tools. The immutable fixture is bound by the runner. '
                'Call charge_payment exactly once, then inspect_transaction, then finish. '
                'Do not change input or repeat a charge. A rejected card is a completed charge attempt.\n'
                + json.dumps({'case': case, 'input': redact(payload), 'tool_history': workflow}), ACTION_SCHEMA)
            tool = action['tool']
            item = {'step': step+1, 'action': action}
            workflow.append(item)
            if tool == 'charge_payment' and not charged:
                charged = True
                result = await agent.run(**payload)
                item['observation'] = redact(result.get('data', {}))
            elif tool == 'inspect_transaction' and charged and not inspected:
                inspected = True
                persistence = await verify_persistence(collection, result, payload, before_count)
                item['observation'] = persistence
            elif tool == 'finish' and charged and inspected:
                finished = True
                break
            else:
                raise ValueError(f'Invalid workflow tool ordering: {tool}')
        if not finished:
            raise ValueError('LLM workflow exceeded its step bound')
        success = result.get('data', {}).get('success')
        if success:
            uuid.UUID(result['data']['transaction_id'])
        status = 'pass' if success is expected['should_succeed'] and persistence['verified'] else 'fail'
    except Exception as exc:
        error = {'type': type(exc).__name__, 'message': redact(str(exc))}
    calls = bridge.calls[call_start:]
    if not result.get('lkw') and recorder.checkpoints.get(case['id']):
        result['lkw'] = recorder.checkpoints[case['id']]
    checkpoints, last_well, first_bad = checkpoint_validation(result, payload, expected, persistence, calls)
    # Missing execution is inconclusive, not a fabricated clean trace.
    analysis = {'status': 'pending'}
    evidence = {'status': status, 'expected': expected, 'checkpoints': checkpoints,
                'last_known_well': last_well, 'first_deviation': first_bad,
                'persistence': persistence, 'rpc_calls': calls, 'error': error}
    try:
        answer = llm.ask('analysis:'+case['id'],
                         'Analyze this executed payment trace. Use checkpoint names and recorded evidence only. '
                         'Report inconclusive if execution evidence is missing.\n'+json.dumps(redact(evidence)), ANALYSIS_SCHEMA)
        analysis = {'status': 'completed', 'result': answer,
                    'agrees_with_oracle': answer['verdict'] == ('inconclusive' if status == 'error' else status),
                    'agrees_with_lkw': answer['last_known_well'] == last_well and answer['first_deviation'] == first_bad}
    except Exception as exc:
        analysis = {'status': 'error', 'error': redact(str(exc))}
    return {'id': case['id'], 'case': case, 'payload': redact(payload), 'status': status,
            'elapsed_ms': round(1000*(time.monotonic()-start), 2), 'expected': expected,
            'workflow': workflow, 'rpc_calls': calls, 'persistence': persistence, 'checkpoints': checkpoints,
            'last_known_well': last_well, 'first_deviation': first_bad, 'error': error,
            'llm_analysis': analysis, 'events': recorder.events[event_start:]}


def compact_targets(coverage, limit=20):
    nodes = {n['id']: n for c in coverage['catalogs'] for n in c['nodes']}
    return [{'id': p['id'], 'variable': p['variable'], 'kind': p['use_kind'],
             'definition': p['definition'], 'use': p['use'],
             'source': nodes[p['use'].split('|')[0]]['source']}
            for p in coverage['uses'] if not p['tests']][:limit]


def source_manifest(repo):
    files = ['src/paymentagent/app/agent.py', 'src/paymentagent/app/repository.py',
             'src/paymentagent/app/fault_injection.py', 'src/paymentagent/app/tools.py',
             'src/paymentservice/charge.js', 'src/paymentservice/server.js',
             'src/paymentservice/package.json', 'src/paymentservice/proto/demo.proto']
    return {name: sha256(repo / name) for name in files}


async def run(args):
    if not os.getenv('SLURM_JOB_ID') and not args.allow_non_slurm:
        raise RuntimeError('Run experiments inside a SPEED SLURM allocation; non-SLURM execution is disabled by default.')
    if socket.gethostname().startswith('speed-submit'):
        raise RuntimeError('Experiments must not run on a SPEED submit node.')
    repo = args.repo.resolve()
    run_id = 'run_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    out = args.output.resolve() / run_id
    out.mkdir(parents=True, exist_ok=False)
    manifest = source_manifest(repo)
    os.environ['FAULT_MODE'] = 'NONE'
    os.environ['BOUNDARY_EVENTS_ENABLED'] = '0'
    os.environ.pop('BOUNDARY_EVENTS_FILE', None)
    # Each run has its own database. Existing application DBs are never cleared.
    db_name = 'payment_dataflow_test_'+run_id
    os.environ['MONGO_URI'], os.environ['MONGO_DB_NAME'] = args.mongo_uri, db_name
    prepared, catalogs = [], []
    for module, relative, functions in [
        ('app.repository','src/paymentagent/app/repository.py',{'save_transaction'}),
        ('app.agent','src/paymentagent/app/agent.py',{'run'})]:
        code, cats = prepare_python(repo / relative, relative, functions)
        prepared.append((module, code)); catalogs.extend(cats)
    service = json.loads(args.service_catalog.read_text(encoding='utf-8'))
    if service['source_sha256'] != manifest['src/paymentservice/charge.js']:
        raise RuntimeError('PaymentService catalog does not match the current charge.js source')
    catalogs.append(service)
    recorder = Recorder(catalogs)
    metadata = {'schema_version': 1, 'run_id': run_id, 'started_at': now(), 'execution': 'SPEED_SLURM' if os.getenv('SLURM_JOB_ID') else 'NON_SLURM_EXPLICIT',
                'slurm_job_id': os.getenv('SLURM_JOB_ID'), 'hostname': socket.gethostname(),
                'python_version': platform.python_version(), 'model': args.model, 'fault_mode': 'NONE',
                'source_hashes': manifest, 'database': db_name, 'status': 'running',
                'adaptations': ['Original PaymentAgent.run AST instrumented in memory.',
                                'charge_payment rebound in memory to real PaymentService gRPC bridge.',
                                'Original repository.save_transaction uses real isolated MongoDB.',
                                'Original charge.js runs as an instrumented test copy in a test RPC host.',
                                'Separate LLM tool workflow replaces production LangGraph entry for this experiment.'],
                'excluded': ['Production HTTP router/LangGraph graph', 'Original local Python charge tool',
                             'Original PaymentService server handler', 'Other agents', 'Third-party library internals']}
    tests, generation = [], []
    coverage = build_coverage(catalogs, [], args.max_paths)
    sources = {name: (repo / name).read_text(encoding='utf-8-sig') for name in manifest if name.endswith(('.py','.js'))}
    llm = Ollama(args.ollama_url, args.model, args.llm_timeout, args.context)
    def snapshot():
        write_json(out/'generated_tests.json', generation)
        write_json(out/'execution_traces.json', [{k:v for k,v in t.items() if k != 'llm_analysis'} for t in tests])
        write_json(out/'coverage.json', coverage)
        write_json(out/'llm_analysis.json', [{'id':t['id'], **t['llm_analysis']} for t in tests])
        write_json(out/'llm_calls.json', llm.calls)
        report = {'metadata': metadata, 'tests': tests, 'coverage': coverage, 'sources': sources,
                  'summary': {s:sum(t['status']==s for t in tests) for s in ['pass','fail','error']}}
        write_json(out/'report.json', report)
        return report
    snapshot()
    bridge = None
    database_client = None
    try:
        metadata['llm_provenance'] = llm.provenance()
        job_runtime = args.service_catalog.resolve().parent.parent
        metadata['runtime_evidence'] = {name: (job_runtime/name).read_text(encoding='utf-8')
                                        for name in ['gpu.txt', 'gpu-details.txt', 'container-sha256.txt', 'python-environment.txt']
                                        if (job_runtime/name).exists()}
        agent_module, repository, fi = load_instrumented(repo, recorder, prepared)
        database_client = importlib.import_module('app.database').client
        await database_client.admin.command('ping')
        collection = importlib.import_module('app.database').transactions_collection
        bridge = Bridge(args.payment_target)
        agent_module.charge_payment = bridge.charge
        agent = agent_module.PaymentAgent()
        seen = set()
        # Deterministic baseline then LLM-generated rounds targeting current gaps.
        for round_no in range(args.rounds+1):
            if round_no == 0:
                cases = baseline_cases()
            else:
                target_info = compact_targets(coverage)
                answer = llm.ask('generation:'+str(round_no),
                    'Generate up to '+str(args.generated_per_round)+' diverse payment tests targeting these uncovered DU pairs. '
                    'Use only synthetic card profiles. There is no fault injection. Do not provide full card numbers. '
                    'Targets are intentions; actual coverage is computed from instrumentation. '
                    'Include accepted/rejected cards, expiration boundaries and money boundaries.\n'
                    + json.dumps({'targets': target_info, 'already_run': [{k:t['case'][k] for k in ('id','card_profile','expiry','currency_code','units','nanos')} for t in tests],
                                  'source': {'PaymentAgent.run': (repo/'src/paymentagent/app/agent.py').read_text(),
                                             'PaymentService.charge': (repo/'src/paymentservice/charge.js').read_text()},
                                  'contract': expectation(baseline_cases()[0])['policy']}), GENERATION_SCHEMA)
                cases = answer['tests'][:args.generated_per_round]
                valid_targets = {u['id'] for u in coverage['uses']}
                for index, case in enumerate(cases):
                    case['id'] = f'llm_r{round_no}_{index+1}_{case["id"]}'[:100]
                    case['origin'] = 'llm_generated'
                    case['invalid_target_ids'] = [t for t in case['targets'] if t not in valid_targets]
            generation.append({'round': round_no, 'tests': cases})
            for case in cases:
                if case['id'] in seen:
                    raise ValueError('Duplicate test ID')
                seen.add(case['id'])
                print(f"Running {case['id']} on {socket.gethostname()}", flush=True)
                test = await execute_case(case, agent, collection, bridge, recorder, llm)
                tests.append(test)
                service_events = []
                if args.service_events.exists():
                    service_events = [json.loads(line) for line in args.service_events.read_text().splitlines() if line.strip()]
                coverage = build_coverage(catalogs, recorder.events+service_events, args.max_paths)
                test['service_events'] = [e for e in service_events if e['test_id'] == case['id']]
                test['covered_nodes'] = sorted(n for n, ids in coverage['node_hits'].items() if case['id'] in ids)
                snapshot()
        metadata['status'] = 'completed'
    except Exception as exc:
        metadata['status'] = 'error'
        metadata['error'] = {'type': type(exc).__name__, 'message': redact(str(exc))}
    finally:
        if bridge:
            bridge.channel.close()
        if database_client:
            database_client.close()
        metadata['finished_at'] = now()
        metadata['source_unchanged'] = source_manifest(repo) == manifest
        report = snapshot()
        from .report import export_html
        export_html(report, out/'dashboard.html')
        print('Results: '+str(out), flush=True)
    incomplete = metadata['status'] != 'completed' or any(t['status']=='error' or t['llm_analysis']['status']!='completed' for t in tests)
    return 2 if incomplete else 1 if any(t['status']=='fail' for t in tests) else 0


def main():
    parser = argparse.ArgumentParser(description='Payment dataflow experiment (SPEED)')
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--service-catalog', type=Path, required=True)
    parser.add_argument('--service-events', type=Path, required=True)
    parser.add_argument('--payment-target', default='127.0.0.1:50061')
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27027')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11444')
    parser.add_argument('--model', default='qwen2.5-coder:14b')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--generated-per-round', type=int, default=6)
    parser.add_argument('--max-paths', type=int, default=50000)
    parser.add_argument('--llm-timeout', type=int, default=900)
    parser.add_argument('--context', type=int, default=8192)
    parser.add_argument('--allow-non-slurm', action='store_true', help='Explicit opt-out; not used in SPEED scripts')
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10 or not 1 <= args.generated_per_round <= 20 or args.max_paths < 1:
        parser.error('rounds=1..10, generated-per-round=1..20, max-paths>=1 required')
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(run(args)))


if __name__ == '__main__':
    main()
