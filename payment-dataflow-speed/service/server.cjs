/* Test-only RPC host for the original charge() implementation, instrumented. */
'use strict';
const path = require('node:path');
const fs = require('node:fs');
const grpc = require('@grpc/grpc-js');
const loader = require('@grpc/proto-loader');
const trace = require('./trace.cjs');
const charge = require('./charge.instrumented.cjs');
const definitions = loader.loadSync(path.join(process.env.DF_PAYMENT_SOURCE,'proto','demo.proto'),
  {keepCase:true,longs:String,enums:String,defaults:true,oneofs:true});
const pkg = grpc.loadPackageDefinition(definitions).hipstershop;
const server = new grpc.Server();
server.addService(pkg.PaymentService.service, {charge(call,callback) {
  const id = call.metadata.get('x-dataflow-test-id')[0] ||
    (process.env.DF_ACTIVE_TEST && fs.existsSync(process.env.DF_ACTIVE_TEST) ? fs.readFileSync(process.env.DF_ACTIVE_TEST,'utf8').trim() : '');
  if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(id))
    return callback({code:grpc.status.INVALID_ARGUMENT,details:'Missing/invalid test ID'});
  trace.context.run(id,() => {
    try {callback(null,charge(call.request));}
    catch (error) {
      // Preserve original error code/details behavior. code 400 is not a standard gRPC status.
      callback(error);
    }
  });
}});
server.bindAsync(`127.0.0.1:${process.env.DF_PAYMENT_PORT || 50061}`,
  grpc.ServerCredentials.createInsecure(),error => {
    if (error) throw error;
    console.log('Dataflow PaymentService ready (test copy, loopback only)');
  });
process.on('SIGTERM',() => server.tryShutdown(() => process.exit(0)));

