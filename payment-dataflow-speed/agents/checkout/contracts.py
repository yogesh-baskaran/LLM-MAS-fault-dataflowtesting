from agents.shared import check


def validate(inputs,response,expected_items,products,rates,evidence,tool_calls,order_rows):
    methods=[c['method'].split('/')[-1] for c in evidence]
    tools=[e.get('data',{}).get('tool') for e in tool_calls]
    checks=[check('native_checkout_used_tools',bool(tools),'>0',len(tools)),
            check('cart_was_read','get_cart' in tools),
            check('checkout_record_persisted',len(order_rows)==(1 if response is not None else 0),1 if response is not None else 0,len(order_rows))]
    if response is None:
        return checks+[check('invalid_order_rejected',not expected_items)]
    order=response.order
    actual={item.item.product_id:item.item.quantity for item in order.items}
    checks.extend([check('nonempty_order_id',bool(order.order_id)),check('returned_items_match_cart',actual==expected_items,expected_items,actual),
                   check('shipment_tracking_matches_service',order.shipping_tracking_id=='TEST-TRACK-001','TEST-TRACK-001',order.shipping_tracking_id),
                   check('order_address_preserved',order.shipping_address.city=='Montreal'),
                   check('confirmation_service_called','SendOrderConfirmation' in methods),
                   check('cart_emptied','empty_cart' in tools)])
    return checks
