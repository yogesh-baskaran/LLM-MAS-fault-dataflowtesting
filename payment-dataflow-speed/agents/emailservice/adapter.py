import os
from agents.shared import checkpoint,check
from .contracts import validate


class Adapter:
    def __init__(self,runtime):
        self.rt=runtime
        os.environ.update(USE_LLM='true',MODEL_NAME=runtime.args.model,OLLAMA_BASE_URL=runtime.args.ollama_url,
                          EMAILSERVICE_HOST='127.0.0.1',EMAILSERVICE_PORT=str(runtime.args.email_port))
        modules=runtime.load_python('emailserviceagent');runtime.load_proto()
        self.graph=modules['app.graph'].build_graph()

    def execute(self,case):
        inputs=case['input'];test=self.rt.test_id
        payload={**inputs,'email':test+'@example.invalid','order_id':'ORDER-'+test,'currency_code':'USD',
                 'total':inputs['total_cents']/100,'items':[{'name':'Fixture item '+str(i),'quantity':1,'price':5} for i in range(inputs['item_count'])],
                 'shipping_address':{'street_address':'1 Test Street','city':'Montreal','state':'QC','country':'CA','zip_code':12345}}
        result=self.graph.invoke({'request':payload})
        calls=self.rt.evidence();generation,delivery=validate(payload,result,calls)
        self.rt.checkpoints=[checkpoint('TASK_START',payload,[check('fixture_bound',True)]),
                             checkpoint('EMAIL_GENERATED',{'subject':result.get('subject'),'body':result.get('body'),'llm_used':result.get('llm_used')},generation),
                             checkpoint('EMAIL_SENT',{'service_calls':calls},delivery),
                             checkpoint('FINAL_ANSWER',{'status':result.get('microservice_status')},generation+delivery)]
        return {'result':result,'checks':generation+delivery,'delivery_mode':'original_demo_dummy_service',
                'known_gap':'Generated content is not passed to the service; original fractional-total and item loss remain unchanged.'}
