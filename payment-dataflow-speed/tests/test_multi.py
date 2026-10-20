"""Synthetic framework/contract checks; no agent workflow or inference execution."""
import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest

from jsonschema import validate
from agents.shared import load_products,check
from dataflow.coverage import build_coverage
from dataflow.common import redact
from dataflow.suite import select_agents,combine,AGENTS
from test_core import synthetic


class ExtendedCoverageTests(unittest.TestCase):
    def test_for_loop_paths_and_redefinitions(self):
        module,recorder,catalogs=synthetic('def sample(xs):\n total=0\n for x in xs:\n  total=total+x\n return total\n')
        recorder.test_id='loop';self.assertEqual(module.sample([1,2]),3)
        result=build_coverage(catalogs,recorder.events,max_node_visits=3)
        self.assertEqual(result['observed_paths_missing_from_cfg'],[])
        self.assertTrue(any(u['variable']=='x' and u['tests'] for u in result['uses']))

    def test_while_continue_preserves_behavior_and_graph(self):
        module,recorder,catalogs=synthetic('def sample(x):\n total=0\n while x>0:\n  x-=1\n  if x==1:\n   continue\n  total+=x\n return total\n')
        recorder.test_id='while';self.assertEqual(module.sample(3),2)
        result=build_coverage(catalogs,recorder.events,max_node_visits=5)
        self.assertEqual(result['observed_paths_missing_from_cfg'],[])

    def test_with_binding_is_defined_after_context_entry(self):
        module,recorder,catalogs=synthetic('def sample(manager):\n with manager as value:\n  return value+1\n')
        class Manager:
            def __enter__(self):return 4
            def __exit__(self,*args):return False
        recorder.test_id='context';self.assertEqual(module.sample(Manager()),5)
        result=build_coverage(catalogs,recorder.events)
        self.assertEqual(result['observed_paths_missing_from_cfg'],[])
        self.assertTrue(next(d for d in result['definitions'] if d['variable']=='value')['tests'])

    def test_pair_denominator_survives_path_truncation(self):
        module,recorder,catalogs=synthetic('def sample(x):\n y=x+1\n z=y+2\n return x+y+z\n')
        full=build_coverage(catalogs,[]);limited=build_coverage(catalogs,[],max_paths=1)
        self.assertTrue(limited['path_enumeration_truncated'])
        self.assertEqual(full['all_definitions']['total_candidates'],limited['all_definitions']['total_candidates'])
        self.assertEqual(full['all_uses']['total_candidates'],limited['all_uses']['total_candidates'])

    def test_long_paths_explicitly_outside_bound(self):
        module,recorder,catalogs=synthetic('def sample(xs):\n total=0\n for x in xs:\n  total+=x\n return total\n')
        recorder.test_id='long';module.sample([1,2,3,4,5])
        result=build_coverage(catalogs,recorder.events,max_node_visits=2)
        self.assertGreater(result['observed_du_paths_outside_bound'],0)
        self.assertFalse(result['complete_feasible_coverage_claim'])


class SuiteTests(unittest.TestCase):
    def test_baseline_cases_validate_for_all_new_agents(self):
        for name in AGENTS[1:]:
            definition=importlib.import_module('agents.'+name+'.cases')
            for case in definition.baseline_cases():validate(case['input'],definition.SCHEMA)

    def test_agent_selection_rejects_unknown_and_duplicates(self):
        self.assertEqual(select_agents('all'),AGENTS)
        self.assertEqual(select_agents('cart,currency'),['cart','currency'])
        for bad in ['','payment,payment','shipping']:
            with self.assertRaises(ValueError):select_agents(bad)

    def test_combined_report_keeps_agent_identity(self):
        cov=build_coverage([],[])
        reports={'cart':{'metadata':{},'tests':[{'id':'cart_one','status':'fail'}],'coverage':cov,'sources':{}},
                 'currency':{'metadata':{},'tests':[{'id':'currency_one','status':'pass'}],'coverage':cov,'sources':{}}}
        combined=combine(reports,{})
        self.assertEqual(combined['summary'],{'pass':1,'fail':1,'error':0})
        self.assertEqual([t['agent'] for t in combined['tests']],['cart','currency'])

    def test_camel_case_catalog_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'src/productcatalogservice';path.mkdir(parents=True)
            (path/'products.json').write_text(json.dumps({'products':[{'id':'P','priceUsd':{'currencyCode':'USD','units':1,'nanos':5}}]}))
            product=load_products(root)[0]
            self.assertEqual(product['price_usd']['currency_code'],'USD')
            self.assertNotIn('priceUsd',product)

    def test_email_contract_exposes_original_payload_loss(self):
        from agents.emailservice.contracts import validate as email_validate
        payload={'email':'test@example.invalid','order_id':'O','total':10.75,'items':[{'name':'item'}]}
        state={'subject':'Order O','body':'Order O confirmed','llm_used':True}
        calls=[{'method':'/hipstershop.EmailService/SendOrderConfirmation','request':{'email':payload['email'],'order':{'order_id':'O','shipping_cost':{'units':10,'nanos':0},'items':[]}}}]
        generation,delivery=email_validate(payload,state,calls)
        self.assertTrue(all(c['valid'] for c in generation))
        failures={c['name'] for c in delivery if not c['valid']}
        self.assertIn('fractional_total_preserved',failures);self.assertIn('order_items_preserved',failures)

    def test_recommendations_validated_without_fixed_random_order(self):
        from agents.recommendation.contracts import validate as rec_validate
        result={'action':'get_recommendations','input_product_ids':['P1'],'recommended_product_ids':['P3','P2']}
        checks=rec_validate({'action':'get_recommendations'},result,['P1','P2','P3'],['P1'])
        self.assertTrue(all(c['valid'] for c in checks))

    def test_mongo_credentials_not_exported_in_source_snapshots(self):
        value=redact('const uri = "mongodb+srv://example:secret@host/db"')
        self.assertNotIn('secret',value);self.assertNotIn('example:',value)

    def test_checkout_startup_adaptation_is_test_only_and_explicit(self):
        from dataflow.prepare_native import adapt_checkout_startup
        original='const (\n listenPort = "5050"\n)\nmongoURI := mustGetEnv("MONGO_URI", "mongodb://example.invalid")\nsrv := grpc.NewServer()'
        changed=adapt_checkout_startup(original)
        self.assertIn('DF_CHECKOUT_PORT',changed)
        self.assertIn('grpc.UnaryInterceptor(df.Unary)',changed)
        self.assertNotIn('example.invalid',changed)
        self.assertIn('"5050"',original)


if __name__=='__main__':unittest.main()
