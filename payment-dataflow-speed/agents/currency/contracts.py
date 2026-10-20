from decimal import Decimal
from agents.shared import check, money


def validate(inputs, result, rates):
    if inputs['action']=='get_supported_currencies':
        return [check('supported_currency_set',set(result['data'])==set(rates),sorted(rates),result['data'])]
    valid=inputs['from_currency'] in rates and inputs['to_currency'] in rates and inputs['units']>=0 and 0<=inputs['nanos']<10**9
    if not valid:
        return [check('invalid_input_rejected',False,'rejection',result.get('data'))]
    observed=result['data']
    expected=(Decimal(inputs['units'])+Decimal(inputs['nanos'])/Decimal(10**9))/Decimal(str(rates[inputs['from_currency']]))*Decimal(str(rates[inputs['to_currency']]))
    return [check('target_currency_preserved',observed['currency_code']==inputs['to_currency'],inputs['to_currency'],observed['currency_code']),
            check('conversion_within_one_micro_unit',abs(money(observed)-expected)<=Decimal('0.000001'),str(expected),str(money(observed))),
            check('normalized_nanos',0<=observed['nanos']<10**9)]
