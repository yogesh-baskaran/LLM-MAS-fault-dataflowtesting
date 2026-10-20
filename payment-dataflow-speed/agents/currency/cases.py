from agents.shared import item_schema

SCHEMA = item_schema({'action': {'enum':['convert','get_supported_currencies']},
                      'from_currency': {'type':'string','maxLength':5},
                      'to_currency': {'type':'string','maxLength':5},
                      'units': {'type':'integer','minimum':-1,'maximum':100000},
                      'nanos': {'type':'integer','minimum':-1,'maximum':1000000000}})


def baseline_cases():
    cases=[]
    for name,action,source,target,units,nanos in [
        ('supported','get_supported_currencies','USD','EUR',0,0),
        ('usd_eur','convert','USD','EUR',10,750000000),
        ('identity','convert','CAD','CAD',2,500000000),
        ('zero','convert','USD','CAD',0,0),
        ('nanos','convert','EUR','USD',0,1),
        ('unsupported','convert','ZZZ','USD',10,0),
        ('negative','convert','USD','EUR',-1,0),
        ('invalid_nanos','convert','USD','EUR',1,1000000000)]:
        cases.append({'id':name,'title':name,'input':dict(action=action,from_currency=source,to_currency=target,units=units,nanos=nanos)})
    return cases
