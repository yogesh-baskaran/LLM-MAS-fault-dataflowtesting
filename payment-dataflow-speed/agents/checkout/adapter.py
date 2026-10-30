from datetime import datetime,timezone
import json
import grpc
from pymongo import MongoClient
from agents.shared import checkpoint,check,read_events,protobuf_dict,load_products
from .contracts import validate


class Adapter:
    def __init__(self,runtime):
        self.rt=runtime;runtime.load_proto();self.stub=runtime.stub('CheckoutService',runtime.args.checkout_port);self.cart=runtime.stub('CartService',runtime.args.cart_port)
        self.products={p['id']:p for p in load_products(runtime.repo)}
        self.ids=list(self.products);self.rates=json.loads((runtime.repo/'src/currencyservice/data/currency_conversion.json').read_text())
        self.mongo=MongoClient(runtime.args.mongo_uri,serverSelectionTimeoutMS=10000);self.mongo.admin.command('ping')

    def execute(self,case):
        pb=self.rt.pb;inputs=case['input'];user=self.rt.test_id;expected={}
        for i in inputs['product_indices']:
            pid=self.ids[i%len(self.ids)];expected[pid]=expected.get(pid,0)+inputs['quantity']
            self.rt.call(self.cart,'AddItem',pb.AddItemRequest(user_id=user,item=pb.CartItem(product_id=pid,quantity=inputs['quantity'])))
        year=datetime.now(timezone.utc).year+2
        request=pb.PlaceOrderRequest(user_id=user,user_currency=inputs['currency'],email=user+'@example.invalid',
            address=pb.Address(street_address='1 Test Street',city='Montreal',state='QC',country='CA',zip_code=12345),
            credit_card=pb.CreditCardInfo(credit_card_number='4111111111111111',credit_card_cvv=123,credit_card_expiration_year=year,credit_card_expiration_month=12))
        self.rt.checkpoints=[checkpoint('TASK_START',protobuf_dict(request),[check('fixture_bound',True)])]
        response=None
        try:response=self.rt.call(self.stub,'PlaceOrder',request)
        except grpc.RpcError as error:
            if error.code() in {grpc.StatusCode.UNAVAILABLE,grpc.StatusCode.DEADLINE_EXCEEDED}:raise
            if expected or error.code() not in {grpc.StatusCode.INVALID_ARGUMENT,grpc.StatusCode.FAILED_PRECONDITION}:raise
        tools=[e for e in read_events(self.rt.args.runtime/'checkout/events.jsonl',self.rt.test_id) if e.get('phase')=='checkpoint']
        rows=list(self.mongo.checkout_agent_db.orders.find({'user_id':user}));rows=[{k:str(v) for k,v in row.items() if k not in {'order_response'}} for row in rows]
        checks=validate(inputs,response,expected,self.products,self.rates,self.rt.evidence(),tools,rows)
        for index,event in enumerate(tools):
            data=event['data']
            if data.get('tool')=='charge_card':
                continue  # Fixture boundary; payment correctness is outside this suite.
            toolchecks=[check('tool_outcome_expected',not data.get('error'))]
            self.rt.checkpoints.append(checkpoint('TOOL_DONE_'+str(index+1),data,toolchecks))
        output=protobuf_dict(response) if response is not None else {'rejected':True}
        self.rt.checkpoints.append(checkpoint('FINAL_ANSWER',output,checks))
        return {'result':output,'checks':checks,'tool_calls':tools,'order_records':rows,'fixtures':['catalog','shipping','payment_success'], 'scope':'checkout_operations_without_payment_validation'}

    def close(self):self.mongo.close()
