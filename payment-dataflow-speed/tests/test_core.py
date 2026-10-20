"""Offline harness unit checks with synthetic functions, not payment experiments."""
import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path

from dataflow.common import redact, write_json
from dataflow.coverage import Recorder, build_coverage, prepare_python
from dataflow.report import export_html
from dataflow.runner import baseline_cases, checkpoint_validation, execute_case, expectation, payload_for


def synthetic(source, function='sample'):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/'synthetic.py'
        path.write_text(source, encoding='utf-8')
        code, catalogs = prepare_python(path, 'synthetic.py', {function})
    recorder = Recorder(catalogs)
    module = types.ModuleType('synthetic')
    module._df_recorder = recorder
    exec(code, module.__dict__)
    return module, recorder, catalogs


class CoverageTests(unittest.TestCase):
    def test_redefinition_kills_old_binding(self):
        module, recorder, catalogs = synthetic('def sample(x):\n y=x+1\n y=x+2\n return y\n')
        recorder.test_id = 'synthetic'
        self.assertEqual(module.sample(2), 4)
        coverage = build_coverage(catalogs, recorder.events)
        y_defs = [d for d in coverage['definitions'] if d['variable']=='y']
        self.assertEqual(len(y_defs),2)
        self.assertFalse(y_defs[0]['tests'])
        self.assertTrue(y_defs[1]['tests'])
        self.assertEqual(coverage['observed_paths_missing_from_cfg'], [])

    def test_predicate_outcomes_are_separate_uses(self):
        module, recorder, catalogs = synthetic('def sample(x):\n if x>0:\n  return x\n return -x\n')
        recorder.test_id='positive';module.sample(1)
        coverage = build_coverage(catalogs,recorder.events)
        predicates = [u for u in coverage['uses'] if u['use_kind']=='predicate']
        self.assertEqual(len(predicates),2)
        self.assertEqual(sum(bool(p['tests']) for p in predicates),1)
        recorder.test_id='negative';module.sample(-1)
        coverage = build_coverage(catalogs,recorder.events)
        self.assertTrue(all(p['tests'] for p in coverage['uses']))
        self.assertEqual(coverage['observed_paths_missing_from_cfg'], [])

    def test_rhs_use_precedes_assignment_definition(self):
        module, recorder, catalogs = synthetic('def sample(x):\n x=x+1\n return x\n')
        recorder.test_id='rhs';self.assertEqual(module.sample(1),2)
        coverage=build_coverage(catalogs,recorder.events)
        self.assertEqual(coverage['all_definitions']['covered'],2)
        self.assertEqual(coverage['observed_paths_missing_from_cfg'], [])

    def test_class_instrumentation_does_not_name_mangle(self):
        module, recorder, catalogs=synthetic('class Example:\n def sample(self,x):\n  return x\n')
        recorder.test_id='class';self.assertEqual(module.Example().sample(3),3)
        self.assertTrue(build_coverage(catalogs,recorder.events)['all_uses']['covered'])

    def test_exception_path_is_recorded(self):
        module, recorder, catalogs=synthetic('def sample(x):\n try:\n  y=10/x\n  return y\n except ZeroDivisionError as error:\n  return str(error)\n')
        recorder.test_id='exception';self.assertIn('zero',module.sample(0))
        coverage=build_coverage(catalogs,recorder.events)
        self.assertEqual(coverage['observed_paths_missing_from_cfg'], [])
        self.assertTrue(any(u['variable']=='error' and u['tests'] for u in coverage['uses']))

    def test_unreached_branch_is_not_covered(self):
        module, recorder, catalogs=synthetic('def sample(x):\n if x:\n  y=x\n  return y\n return 0\n')
        recorder.test_id='false';module.sample(False)
        coverage=build_coverage(catalogs,recorder.events)
        self.assertFalse(next(d for d in coverage['definitions'] if d['variable']=='y')['tests'])
        self.assertFalse(coverage['complete_feasible_coverage_claim'])

    def test_truncation_is_explicit(self):
        module, recorder, catalogs=synthetic('def sample(x):\n y=x\n return x+y\n')
        coverage=build_coverage(catalogs,[],max_paths=1)
        self.assertTrue(coverage['path_enumeration_truncated'])

    def test_unsupported_match_fails_instead_of_claiming_all_paths(self):
        with self.assertRaisesRegex(ValueError,'Unsupported CFG'):
            synthetic('def sample(x):\n match x:\n  case 1: return 1\n return x\n')


class ArtifactTests(unittest.TestCase):
    def test_cards_redacted_but_hashes_preserved(self):
        result=redact({'credit_card_number':'4111111111111111','credit_card_cvv':123,
                       'message':'PAN 4111111111111111 rejected','hash':'a1234567890123456b'})
        self.assertNotIn('4111111111111111',json.dumps(result))
        self.assertEqual(result['hash'],'a1234567890123456b')
        self.assertEqual(result['credit_card_cvv'],'[REDACTED]')

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.json';write_json(path,{'value':1})
            self.assertEqual(json.loads(path.read_text()),{'value':1})
            self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_embedded_report_cannot_break_script_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.html';export_html({'value':'</script><script>bad()</script>'},path)
            text=path.read_text(encoding='utf-8')
            self.assertNotIn('</script><script>bad()',text)
            self.assertIn('\\u003c/script>',text)


class ContractTests(unittest.TestCase):
    def test_negative_amount_rejected_by_independent_policy(self):
        case=next(c for c in baseline_cases() if c['id']=='seed_negative_units')
        self.assertFalse(expectation(case)['should_succeed'])
        self.assertTrue(expectation(case)['card_accepted'])

    def test_expiration_uses_current_clock(self):
        from datetime import datetime,timezone
        case=next(c for c in baseline_cases() if c['id']=='seed_expired_card')
        self.assertLess(payload_for(case)['credit_card_expiration_year'],datetime.now(timezone.utc).year)

    def test_lkw_stops_at_first_invalid_checkpoint(self):
        case=baseline_cases()[0];payload=payload_for(case)
        result={'lkw':[{'step':'TASK_START','data':{'currency_code':payload['currency_code'],'units':payload['units'],'nanos':payload['nanos']}},
                       {'step':'CHARGE_DONE','data':{'transaction_id':'wrong','units_charged':payload['units'],'nanos_charged':payload['nanos']}},
                       {'step':'SAVE_DONE','data':{'saved':True}}]}
        checkpoints,last,bad=checkpoint_validation(result,payload,expectation(case),{'verified':True},[])
        self.assertEqual(last,'TASK_START');self.assertEqual(bad,'CHARGE_DONE')

    def test_premature_llm_finish_is_error_without_forced_charge(self):
        class FakeLLM:
            def ask(self,purpose,prompt,schema):
                if purpose.startswith('workflow'):return {'tool':'finish','reason':'premature'}
                return {'verdict':'inconclusive','last_known_well':None,'first_deviation':None,'explanation':'No execution','evidence':[]}
        class FakeCollection:
            async def count_documents(self,_):return 0
        class FakeAgent:
            async def run(self,**kwargs):raise AssertionError('Must not be executed')
        bridge=types.SimpleNamespace(test_id=None,calls=[])
        recorder=Recorder([])
        result=asyncio.run(execute_case(baseline_cases()[0],FakeAgent(),FakeCollection(),bridge,recorder,FakeLLM()))
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['rpc_calls'],[])
        self.assertTrue(result['llm_analysis']['agrees_with_oracle'])


if __name__=='__main__':
    unittest.main()
