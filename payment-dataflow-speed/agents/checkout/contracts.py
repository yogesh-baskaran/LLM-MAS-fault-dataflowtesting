from decimal import Decimal
from agents.shared import check,money


def validate(inputs,response,expected_items,products,rates,evidence,tool_calls,order_rows):
    methods=[c['method'].split('/')[-1] for c in evidence]
    tools=[e.get('data',{}).get('tool') for e in tool_calls]
    checks=[check('native_checkout_used_tools',bool(tools),'>0',len(tools)),
            check('cart_was_read','get_cart' in tools),
            check('payment_attempt_matches_nonempty_cart',('charge_card' in tools)==bool(expected_items)),
            check('checkout_record_persisted',len(order_rows)==1,1,len(order_rows))]
    if response is None:
        return checks+[check('invalid_order_rejected',inputs['card_profile']=='expired' or not expected_items)]
    order=response.order
    actual={item.item.product_id:item.item.quantity for item in order.items}
    checks.extend([check('nonempty_order_id',bool(order.order_id)),check('returned_items_match_cart',actual==expected_items,expected_items,actual),
                   check('shipment_tracking_matches_service',order.shipping_tracking_id=='TEST-TRACK-001','TEST-TRACK-001',order.shipping_tracking_id),
                   check('order_address_preserved',order.shipping_address.city=='Montreal'),
                   check('confirmation_service_called','SendOrderConfirmation' in methods),
                   check('cart_emptied','empty_cart' in tools)])
    payments=[c['data'] for c in tool_calls if c.get('data',{}).get('tool')=='charge_card']
    checks.append(check('exactly_one_payment_attempt',len(payments)==1,1,len(payments)))
    if payments:
        import json
        raw=payments[-1]['arguments'];payload=json.loads(raw) if isinstance(raw,str) else raw
        total=sum((money(products[pid]['price_usd'])*q for pid,q in expected_items.items()),Decimal(0))+Decimal(5)
        expected=total/Decimal(str(rates['USD']))*Decimal(str(rates[inputs['currency']]))
        charged=Decimal(str(payload.get('amount_units',0)))+Decimal(str(payload.get('amount_nanos',0)))/Decimal(10**9)
        checks.extend([check('payment_total_matches_order',abs(charged-expected)<=Decimal('0.00001'),str(expected),str(charged)),
                       check('payment_currency_preserved',payload.get('currency_code')==inputs['currency'])])
    if inputs['card_profile']=='expired':checks.append(check('expired_card_not_reported_success',False))
    return checks
