from agents.shared import check


def validate(inputs,result,catalog_ids,excluded):
    observed=result['recommended_product_ids'];eligible=set(catalog_ids)-set(excluded)
    return [check('requested_action_preserved',result['action']==inputs['action']),
            check('input_product_ids_preserved',result['input_product_ids']==excluded,excluded,result['input_product_ids']),
            check('recommendations_exist_in_catalog',set(observed)<=set(catalog_ids)),
            check('input_products_excluded',not(set(observed)&set(excluded))),
            check('no_duplicate_recommendations',len(observed)==len(set(observed))),
            check('expected_response_count',len(observed)==min(5,len(eligible)),min(5,len(eligible)),len(observed))]
