import json
import os
import grpc
from agents.shared import checkpoint, check
from .contracts import validate


class Adapter:
    def __init__(self, runtime):
        self.rt=runtime
        os.environ.update(CURRENCY_HOST='127.0.0.1',CURRENCY_PORT=str(runtime.args.currency_port))
        modules=runtime.load_python('currencyagent');runtime.load_proto()
        self.agent=modules['app.agent'].CurrencyAgent()
        self.stub=runtime.stub('CurrencyService',runtime.args.currency_port)
        # The existing agent uses its original client API with an observed real RPC transport.
        self.client=modules['app.agent'].client
        self.client.convert=self.convert
        self.client.get_supported_currencies=lambda:list(runtime.call(self.stub,'GetSupportedCurrencies',runtime.pb.Empty()).currency_codes)
        self.rates=json.loads((runtime.repo/'src/currencyservice/data/currency_conversion.json').read_text())

    def convert(self,currency_code,units,nanos,to_code):
        pb=self.rt.pb
        response=self.rt.call(self.stub,'Convert',pb.CurrencyConversionRequest(**{'from':pb.Money(currency_code=currency_code,units=units,nanos=nanos),'to_code':to_code}))
        return {'currency_code':response.currency_code,'units':response.units,'nanos':response.nanos}

    def execute(self,case):
        inputs=case['input']
        try:
            result=self.agent.run(query='convert amount' if inputs['action']=='convert' else 'available currencies',**inputs)
            checks=validate(inputs,result,self.rates)
        except grpc.RpcError as error:
            invalid=inputs['from_currency'] not in self.rates or inputs['to_currency'] not in self.rates or inputs['units']<0 or not 0<=inputs['nanos']<10**9
            if error.code() in {grpc.StatusCode.UNAVAILABLE,grpc.StatusCode.DEADLINE_EXCEEDED} or not invalid:
                raise
            result={'error':error.details()};checks=[check('invalid_input_rejected',True)]
        self.rt.checkpoints=[checkpoint('TASK_START',inputs,[check('fixture_bound',True)])]
        self.rt.checkpoints.append(checkpoint('CONVERT_DONE',result,checks))
        self.rt.checkpoints.append(checkpoint('FINAL_ANSWER',result,checks))
        return {'result':result,'checks':checks,'original_lkw':result.get('lkw',[])}
