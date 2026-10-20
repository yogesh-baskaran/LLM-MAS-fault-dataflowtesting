from agents.shared import check


def validate_cart(response,expected,user):
    actual={item.product_id:item.quantity for item in response.items}
    return [check('cart_user_preserved',response.user_id==user,user,response.user_id),
            check('cart_state_matches_operations',actual==expected,expected,actual),
            check('all_quantities_positive',all(q>0 for q in actual.values()))]
