from __future__ import annotations

import json
import urllib.request
from jsonschema import validate


class Ollama:
    def __init__(self, base_url, model, timeout=900, context=8192):
        self.base_url = base_url.rstrip('/')
        self.model, self.timeout, self.context = model, timeout, context
        self.calls = []

    def provenance(self):
        result = {'settings': {'temperature': 0, 'seed': 42, 'num_ctx': self.context, 'num_predict': 4096}}
        for endpoint in ['version', 'tags']:
            with urllib.request.urlopen(self.base_url+'/api/'+endpoint, timeout=30) as response:
                payload = json.load(response)
            if endpoint == 'version':
                result['ollama_version'] = payload.get('version')
            else:
                result['model_record'] = next((m for m in payload.get('models', [])
                                                if m.get('name') == self.model or m.get('model') == self.model), None)
                if result['model_record'] is None:
                    raise RuntimeError('Configured model is not present in this Ollama instance; run setup first')
        return result

    def ask(self, purpose, prompt, schema):
        body = {'model': self.model, 'stream': False, 'format': schema,
                'messages': [{'role': 'system', 'content': 'You are a software dataflow testing assistant. Return only JSON satisfying the supplied schema. Never invent execution evidence.'},
                             {'role': 'user', 'content': prompt + '\nSchema: ' + json.dumps(schema)}],
                'options': {'temperature': 0, 'seed': 42, 'num_ctx': self.context, 'num_predict': 4096}}
        request = urllib.request.Request(self.base_url + '/api/chat', json.dumps(body).encode(),
                                         {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            envelope = json.load(response)
        result = json.loads(envelope['message']['content'])
        validate(result, schema)
        self.calls.append({'purpose': purpose, 'model': self.model,
                           'input_tokens': envelope.get('prompt_eval_count'),
                           'output_tokens': envelope.get('eval_count'),
                           'duration_ns': envelope.get('total_duration'),
                           'response': result})
        return result
