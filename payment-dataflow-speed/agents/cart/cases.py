from agents.shared import item_schema
OP=item_schema({'action':{'enum':['add','get','clear']},'user':{'enum':['a','b']},
                'product_index':{'type':'integer','minimum':0,'maximum':2},'quantity':{'type':'integer','minimum':-1,'maximum':10}})
SCHEMA=item_schema({'operations':{'type':'array','minItems':1,'maxItems':12,'items':OP}})


def baseline_cases():
    def op(action,user='a',quantity=1,index=0):return dict(action=action,user=user,quantity=quantity,product_index=index)
    return [{'id':name,'title':name,'input':{'operations':ops}} for name,ops in [
        ('empty',[op('get')]),('add',[op('add'),op('get')]),
        ('merge',[op('add',quantity=2),op('add',quantity=3),op('get')]),
        ('isolation',[op('add'),op('get','b'),op('get')]),
        ('clear',[op('add'),op('clear'),op('get')]),
        ('multiple',[op('add'),op('add',index=1),op('get')]),
        ('invalid_quantity',[op('add',quantity=-1),op('get')])]]
