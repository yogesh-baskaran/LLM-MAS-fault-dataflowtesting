from agents.shared import item_schema
SCHEMA=item_schema({'product_indices':{'type':'array','minItems':0,'maxItems':3,'items':{'type':'integer','minimum':0,'maximum':2}},
                    'quantity':{'type':'integer','minimum':1,'maximum':3},'currency':{'enum':['USD','CAD','EUR']},
                    'card_profile':{'enum':['visa','expired']}})


def baseline_cases():
    return [{'id':name,'title':name,'input':dict(product_indices=indices,quantity=q,currency=currency,card_profile=card)} for name,indices,q,currency,card in [
        ('single',[0],1,'USD','visa'),('multiple',[0,1],2,'USD','visa'),
        ('converted',[1],1,'CAD','visa'),('empty',[],1,'USD','visa'),('expired',[0],1,'USD','expired')]]
