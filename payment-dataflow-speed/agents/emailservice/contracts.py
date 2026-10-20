from decimal import Decimal
from agents.shared import check,money


def validate(payload,state,service_calls):
    generation=[check('llm_generation_completed',state.get('llm_used') is True),
                check('nonempty_subject',bool(state.get('subject','').strip())),
                check('nonempty_body',bool(state.get('body','').strip())),
                check('order_id_in_generated_content',payload['order_id'] in state.get('subject','')+state.get('body',''))]
    calls=[c for c in service_calls if c['method'].endswith('/SendOrderConfirmation')]
    delivery=[check('exactly_one_confirmation_call',len(calls)==1,1,len(calls))]
    if calls:
        sent=calls[0]['request'];order=sent.get('order',{})
        delivery.extend([check('recipient_preserved',sent.get('email')==payload['email']),
                         check('order_id_preserved',order.get('order_id')==payload['order_id']),
                         check('fractional_total_preserved',money(order.get('shipping_cost',{}))==Decimal(str(payload['total'])),payload['total'],str(money(order.get('shipping_cost',{})))),
                         check('order_items_preserved',len(order.get('items',[]))==len(payload['items']),len(payload['items']),len(order.get('items',[]))),
                         check('generated_content_connected_to_delivery',False,'subject/body forwarded','Existing RPC request contains only email and order')])
    return generation,delivery
