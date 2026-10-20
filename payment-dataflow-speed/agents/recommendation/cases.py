from agents.shared import item_schema
SCHEMA=item_schema({'action':{'enum':['get_recommendations','explain_recommendations']},
                    'product_indices':{'type':'array','items':{'type':'integer','minimum':0,'maximum':8},'maxItems':12}})


def baseline_cases():
    return [{'id':name,'title':name,'input':{'action':action,'product_indices':indices}} for name,action,indices in [
        ('empty','get_recommendations',[]),('one','get_recommendations',[0]),
        ('multiple','get_recommendations',[0,1]),('duplicates','get_recommendations',[0,0]),
        ('exclude_catalog','get_recommendations',list(range(9))),('explain','explain_recommendations',[1])]]
