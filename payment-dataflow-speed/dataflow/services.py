"""Isolated catalog/shipping fixtures and original demo email/recommendation handlers.

These fixture boundaries are declared in every checkout/recommendation report.
No SMTP/cloud mail delivery is implemented or invoked.
"""
from __future__ import annotations
import argparse
import ast
from concurrent import futures
import importlib
import json
import logging
import os
from pathlib import Path
import random
import sys
import threading

import grpc
from google.protobuf.json_format import MessageToDict
from dataflow.common import redact,now
from agents.shared import load_products


def extract(path,names,namespace):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    if {n.name for n in selected}!=names:raise ValueError('Original service handlers changed: '+str(path))
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)


class Evidence(grpc.ServerInterceptor):
    def __init__(self,args):self.args=args;self.lock=threading.Lock()
    def intercept_service(self,continuation,details):
        handler=continuation(details)
        if handler is None or handler.unary_unary is None:return handler
        def invoke(request,context):
            test=dict(context.invocation_metadata()).get('x-dataflow-test-id')
            if not test and self.args.active_test.exists():test=self.args.active_test.read_text().strip()
            record={'test_id':test,'phase':'rpc','method':details.method,'request':MessageToDict(request,preserving_proto_field_name=True,always_print_fields_with_no_presence=True),'timestamp':now()}
            try:
                result=handler.unary_unary(request,context)
                record.update(status='ok',response=MessageToDict(result,preserving_proto_field_name=True,always_print_fields_with_no_presence=True))
                return result
            except Exception as error:
                record.update(status='error',error=str(error));raise
            finally:
                with self.lock:
                    with self.args.events.open('a',encoding='utf-8') as stream:stream.write(json.dumps(redact(record))+'\n')
        return grpc.unary_unary_rpc_method_handler(invoke,request_deserializer=handler.request_deserializer,response_serializer=handler.response_serializer)


def main():
    sys.dont_write_bytecode=True
    os.environ['BOUNDARY_EVENTS_ENABLED']='0'
    os.environ.pop('BOUNDARY_EVENTS_FILE',None)
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--active-test',type=Path,required=True);p.add_argument('--events',type=Path,required=True)
    for name,default in [('catalog',50067),('shipping',50068),('email',50064),('recommendation',50065)]:p.add_argument('--'+name+'-port',type=int,default=default)
    args=p.parse_args();sys.path.insert(0,str(args.repo/'src/paymentagent'));sys.path.insert(0,str(args.repo/'src'))
    from app.clients import demo_pb2 as pb,demo_pb2_grpc as rpc
    from boundary_validation import boundary_contract
    products=load_products(args.repo)
    # Fail during service preparation rather than halfway through an experiment.
    product_messages=[pb.Product(**product) for product in products]
    class Catalog(rpc.ProductCatalogServiceServicer):
        def ListProducts(self,request,context):return pb.ListProductsResponse(products=product_messages)
        def GetProduct(self,request,context):
            for product in products:
                if product['id']==request.id:return pb.Product(**product)
            context.abort(grpc.StatusCode.NOT_FOUND,'Unknown fixture product')
        def SearchProducts(self,request,context):return pb.SearchProductsResponse(results=[pb.Product(**product) for product in products if request.query.lower() in product['name'].lower()])
    class CheckoutPaymentFixture(rpc.PaymentServiceServicer):
        # Deliberately no card/amount validation or transaction persistence.
        # Used only by checkout; payment's own suite retains the original service.
        def Charge(self,request,context):
            test=dict(context.invocation_metadata()).get('x-dataflow-test-id')
            if not test and args.active_test.exists():test=args.active_test.read_text().strip()
            return pb.ChargeResponse(transaction_id='CHECKOUT-FIXTURE-'+(test or 'setup'))
    class Shipping(rpc.ShippingServiceServicer):
        def GetQuote(self,request,context):return pb.GetQuoteResponse(cost_usd=pb.Money(currency_code='USD',units=5,nanos=0))
        def ShipOrder(self,request,context):return pb.ShipOrderResponse(tracking_id='TEST-TRACK-001')
    namespace={'__name__':'dataflow_test_services','demo_pb2':pb,'demo_pb2_grpc':rpc,'grpc':grpc,'random':random,'logger':logging.getLogger('test-services'),'boundary_contract':boundary_contract}
    extract(args.repo/'src/emailservice/email_server.py',{'BaseEmailService','DummyEmailService','record_email_boundary'},namespace)
    extract(args.repo/'src/recommendationservice/recommendation_server.py',{'RecommendationService','record_recommendation_boundary'},namespace)
    channel=grpc.insecure_channel(f'127.0.0.1:{args.catalog_port}');namespace['product_catalog_stub']=rpc.ProductCatalogServiceStub(channel)
    servers=[];evidence=Evidence(args)
    for port,register,service in [(args.catalog_port,rpc.add_ProductCatalogServiceServicer_to_server,Catalog()),
                                   (args.shipping_port,rpc.add_ShippingServiceServicer_to_server,Shipping()),
                                   (args.email_port,rpc.add_EmailServiceServicer_to_server,namespace['DummyEmailService']()),
                                   (args.recommendation_port,rpc.add_RecommendationServiceServicer_to_server,namespace['RecommendationService']())]:
        server=grpc.server(futures.ThreadPoolExecutor(max_workers=4),interceptors=[evidence]);register(service,server)
        if port==args.catalog_port:
            rpc.add_PaymentServiceServicer_to_server(CheckoutPaymentFixture(),server)
        if not server.add_insecure_port(f'127.0.0.1:{port}'):raise RuntimeError('Cannot bind test service port '+str(port))
        server.start();servers.append(server)
    print('Original demo handlers and declared catalog/shipping fixtures ready',flush=True);threading.Event().wait()


if __name__=='__main__':main()
