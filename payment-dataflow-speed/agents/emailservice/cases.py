from agents.shared import item_schema
SCHEMA=item_schema({'user_name':{'enum':['Alice','Bob']},'email_type':{'enum':['order_confirmation','order_update']},
                    'total_cents':{'type':'integer','minimum':0,'maximum':100000},'item_count':{'type':'integer','minimum':0,'maximum':3}})


def baseline_cases():
    return [{'id':name,'title':name,'input':dict(user_name=user,email_type=kind,total_cents=total,item_count=count)} for name,user,kind,total,count in [
        ('integer_total','Alice','order_confirmation',1000,1),('fractional_total','Bob','order_confirmation',1075,2),
        ('empty_order','Alice','order_confirmation',0,0),('update','Bob','order_update',500,1)]]
