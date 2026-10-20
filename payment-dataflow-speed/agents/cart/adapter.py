import json
import grpc
from agents.shared import checkpoint,check,protobuf_dict
from .contracts import validate_cart


class Adapter:
    def __init__(self,runtime):
        self.rt=runtime;runtime.load_proto();self.stub=runtime.stub('CartService',runtime.args.cart_port)
        self.ids=[p['id'] for p in json.loads((runtime.repo/'src/productcatalogservice/products.json').read_text())['products']]

    def execute(self,case):
        pb=self.rt.pb;states={'a':{},'b':{}};checks=[];observations=[]
        self.rt.checkpoints=[checkpoint('TASK_START',case['input'],[check('fixture_bound',True)])]
        for op in case['input']['operations']:
            user=self.rt.test_id+'_'+op['user'];state=states[op['user']];product=self.ids[op['product_index']%len(self.ids)]
            if op['action']=='add':
                invalid=op['quantity']<=0
                try:
                    self.rt.call(self.stub,'AddItem',pb.AddItemRequest(user_id=user,item=pb.CartItem(product_id=product,quantity=op['quantity'])))
                    stepchecks=[check('invalid_quantity_rejected',not invalid)]
                    if not invalid:state[product]=state.get(product,0)+op['quantity']
                except grpc.RpcError as error:
                    if not invalid or error.code() not in {grpc.StatusCode.INVALID_ARGUMENT,grpc.StatusCode.FAILED_PRECONDITION}:raise
                    stepchecks=[check('invalid_quantity_rejected',True)]
                step='CART_UPDATED'
            elif op['action']=='clear':
                self.rt.call(self.stub,'EmptyCart',pb.EmptyCartRequest(user_id=user));state.clear();stepchecks=[check('clear_rpc_completed',True)];step='CART_CLEARED'
            else:stepchecks=[];step='CART_READ'
            response=self.rt.call(self.stub,'GetCart',pb.GetCartRequest(user_id=user));stepchecks+=validate_cart(response,state,user)
            observations.append(protobuf_dict(response));checks+=stepchecks
            self.rt.checkpoints.append(checkpoint(step,observations[-1],stepchecks))
        self.rt.checkpoints.append(checkpoint('FINAL_ANSWER',{'carts':observations},checks))
        return {'result':observations,'checks':checks,'state_storage':'original_in_memory_cart'}
