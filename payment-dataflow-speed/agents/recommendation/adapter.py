import json
import os
from agents.shared import checkpoint,check
from .contracts import validate


class Adapter:
    def __init__(self,runtime):
        self.rt=runtime
        os.environ.update(RECOMMENDATION_HOST='127.0.0.1',RECOMMENDATION_PORT=str(runtime.args.recommendation_port))
        modules=runtime.load_python('recommendationagent');runtime.load_proto()
        self.agent=modules['app.agent'].RecommendationAgent()
        self.stub=runtime.stub('RecommendationService',runtime.args.recommendation_port)
        modules['app.agent'].client.list_recommendations=self.fetch
        self.ids=[p['id'] for p in json.loads((runtime.repo/'src/productcatalogservice/products.json').read_text())['products']]

    def fetch(self,user_id,product_ids):
        response=self.rt.call(self.stub,'ListRecommendations',self.rt.pb.ListRecommendationsRequest(user_id=user_id,product_ids=product_ids))
        return list(response.product_ids)

    def execute(self,case):
        excluded=[self.ids[i%len(self.ids)] for i in case['input']['product_indices']]
        result=getattr(self.agent,case['input']['action'])(self.rt.test_id,excluded)
        checks=validate(case['input'],result,self.ids,excluded)
        self.rt.checkpoints=[checkpoint('TASK_START',{'product_ids':excluded},[check('fixture_bound',True)]),
                             checkpoint('RECOMMEND_DONE',result,checks),checkpoint('FINAL_ANSWER',result,checks)]
        return {'result':result,'checks':checks,'original_lkw':result.get('lkw',[])}
