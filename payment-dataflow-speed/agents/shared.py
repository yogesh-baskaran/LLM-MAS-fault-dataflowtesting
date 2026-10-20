from __future__ import annotations

import ast
import importlib
import json
import logging
import sys
import types
from decimal import Decimal
from pathlib import Path

import grpc
from google.protobuf.json_format import MessageToDict

from dataflow.common import redact, now
from dataflow.coverage import prepare_python, Recorder


def protobuf_dict(message):
    return MessageToDict(message, preserving_proto_field_name=True,
                         always_print_fields_with_no_presence=True)


def money(value):
    return Decimal(str(value.get('units', 0))) + Decimal(str(value.get('nanos', 0)))/Decimal(10**9)


def load_products(repo):
    products=json.loads((repo/'src/productcatalogservice/products.json').read_text())['products']
    normalized=[]
    for product in products:
        product=dict(product);price=dict(product.pop('priceUsd',product.get('price_usd',{})))
        price['currency_code']=price.pop('currencyCode',price.get('currency_code','USD'))
        product['price_usd']=price;normalized.append(product)
    return normalized


def check(name, valid, expected=None, observed=None):
    return {'name': name, 'valid': bool(valid), 'expected': expected, 'observed': observed}


def checkpoint(step, data, checks):
    return {'step': step, 'timestamp': now(), 'source': 'test_boundary', 'data': redact(data),
            'validation': {'valid': all(c['valid'] for c in checks), 'reason': '; '.join(c['name'] for c in checks),
                           'checks': checks}}


def read_events(path, test_id=None):
    if not path or not Path(path).exists():
        return []
    events = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            event = json.loads(line)
            if test_id is None or event.get('test_id') == test_id:
                events.append(event)
    return events


class Runtime:
    def __init__(self, args, scope):
        self.args, self.repo, self.scope = args, args.repo, scope
        self.catalogs, self.prepared, self.sources = [], [], {}
        self.recorder = None
        self.rpc_calls, self.channels, self.checkpoints = [], [], []
        self.pb = self.stubs = None
        self.test_id = None

    def prepare(self):
        for item in self.scope.get('python', []):
            relative = item['file']
            code, catalogs = prepare_python(self.repo/relative, relative, set(item['functions']))
            self.catalogs.extend(catalogs)
            self.prepared.append((item['module'], relative, code))
            self.sources[relative] = (self.repo/relative).read_text(encoding='utf-8-sig')
        for filename in self.scope.get('native_catalogs', []):
            path = self.args.runtime/filename
            catalogs = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(catalogs, dict):
                catalogs = [catalogs]
            self.catalogs.extend(catalogs)
            for catalog in catalogs:
                for node in catalog['nodes']:
                    relative = node['file']
                    if (self.repo/relative).is_file():
                        self.sources[relative] = redact((self.repo/relative).read_text(encoding='utf-8-sig'))
        self.recorder = Recorder(self.catalogs)

    def load_python(self, agent_directory):
        # One OS process per suite avoids collisions between existing `app` packages.
        sys.dont_write_bytecode=True
        sys.path.insert(0, str(self.repo/'src'/agent_directory))
        sys.path.insert(0, str(self.repo/'src'))
        if agent_directory == 'emailserviceagent':
            # Original client constructs an incorrect src/src/emailservice import path.
            sys.path.insert(0, str(self.repo/'src'/'emailservice'))
        importlib.import_module('app')
        modules = {}
        for name, relative, code in self.prepared:
            module = types.ModuleType(name)
            module.__file__, module.__package__ = str(self.repo/relative), 'app'
            module._df_recorder = self.recorder
            sys.modules[name] = module
            exec(code, module.__dict__)
            modules[name] = module
        fi = importlib.import_module('app.fault_injection')
        fi.FAULT_MODE = 'NONE'
        return modules

    def load_proto(self):
        # Run after load_python for Python suites, so the agent owns its protobuf import.
        if 'app.clients.demo_pb2' in sys.modules:
            self.pb, self.stubs = sys.modules['app.clients.demo_pb2'], importlib.import_module('app.clients.demo_pb2_grpc')
        elif 'demo_pb2' in sys.modules:
            self.pb, self.stubs = sys.modules['demo_pb2'], importlib.import_module('demo_pb2_grpc')
        else:
            sys.path.insert(0, str(self.repo/'src'/'paymentagent'))
            self.pb = importlib.import_module('app.clients.demo_pb2')
            self.stubs = importlib.import_module('app.clients.demo_pb2_grpc')

    def stub(self, service, port):
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        grpc.channel_ready_future(channel).result(timeout=30)
        self.channels.append(channel)
        return getattr(self.stubs, service+'Stub')(channel)

    def call(self, stub, method, message):
        record = {'test_id': self.test_id, 'method': method, 'request': protobuf_dict(message), 'transport': 'real_grpc'}
        try:
            result = getattr(stub, method)(message, timeout=self.args.rpc_timeout,
                                           metadata=[('x-dataflow-test-id', self.test_id)])
            record.update(status='ok', response=protobuf_dict(result))
            return result
        except grpc.RpcError as error:
            record.update(status='rpc_error', code=error.code().name, details=error.details())
            raise
        finally:
            self.rpc_calls.append(redact(record))

    def start_case(self, test_id):
        self.test_id = self.recorder.test_id = test_id
        self.args.active_test.write_text(test_id, encoding='utf-8')
        self.rpc_calls, self.checkpoints = [], []

    def events(self, test_id):
        events = [e for e in self.recorder.events if e.get('test_id') == test_id]
        for name in self.scope.get('event_files', []):
            events.extend(read_events(self.args.runtime/name, test_id))
        return events

    def evidence(self):
        return read_events(self.args.runtime/'rpc.jsonl', self.test_id)

    def close(self):
        for channel in self.channels:
            channel.close()


def item_schema(properties, required=None):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': required or list(properties)}
