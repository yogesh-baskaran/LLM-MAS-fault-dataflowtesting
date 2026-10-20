// Original currency algorithms extracted into a loopback-only test RPC host.
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const parser=require('@babel/parser'),generate=require('@babel/generator').default,t=require('@babel/types');
const original=fs.readFileSync(process.env.DF_CURRENCY_ORIGINAL,'utf8');
const ast=parser.parse(original,{sourceType:'script'});
const functions=ast.program.body.filter(n=>t.isFunctionDeclaration(n)&&['_getCurrencyData','_carry','getSupportedCurrencies','convert'].includes(n.id.name));
if(functions.length!==4)throw Error('Currency implementation changed; review extracted test host');
const carry=functions.find(n=>n.id.name==='_carry'),filename='src/currencyservice/server.js',scope=filename+':_carry';
const nodes=[],edges=[];let previous;
function node(s,kind,defs,uses){const id=`${scope}:${s.loc.start.line}:${nodes.length+1}`;nodes.push({id,scope,file:filename,line:s.loc.start.line,kind,definitions:defs,uses,use_kind:'computational',source:original.slice(s.start,s.end).split('\n')[0]});if(previous)edges.push([previous,id]);previous=id;return id;}
const entry=node(carry,'entry',['amount'],[]);
const enter=parser.parse(`const _dfFrame=trace.enter(${JSON.stringify(scope)},${JSON.stringify(entry)});`).program.body[0];
const hit=(id,phase)=>parser.parse(`trace.event(_dfFrame,${JSON.stringify(id)},${JSON.stringify(phase)});`).program.body[0];
const body=[enter];
for(const s of carry.body.body){const defs=t.isVariableDeclaration(s)?s.declarations.map(d=>d.id.name):[];const text=generate(s).code;const uses=['amount','fractionSize'].filter(name=>new RegExp('\\b'+name+'\\b').test(text)&&!defs.includes(name));const id=node(s,t.isReturnStatement(s)?'return':'statement',defs,uses);body.push(hit(id,'node'),hit(id,'use'),s);if(defs.length)body.push(hit(id,'def'));}
carry.body.body=body;
fs.writeFileSync(process.env.DF_CURRENCY_CATALOG,JSON.stringify([{scope,entry,nodes,edges}],null,2));
const req=createRequire(path.join(process.env.DF_CURRENCY_SOURCE,'server.js'));
const trace=require(path.join(process.env.DF_ROOT,'service','trace.cjs'));
const context={require:req,logger:{info(){},error(){}},trace};vm.createContext(context);vm.runInContext(functions.map(f=>generate(f).code).join('\n'),context);
const grpc=req('@grpc/grpc-js'),loader=req('@grpc/proto-loader');
const proto=grpc.loadPackageDefinition(loader.loadSync(path.join(process.env.DF_CURRENCY_SOURCE,'proto/demo.proto'),{keepCase:true,longs:String,defaults:true})).hipstershop;
function handler(fn){return(call,callback)=>{let id=call.metadata.get('x-dataflow-test-id')[0];if(!id&&process.env.DF_ACTIVE_TEST&&fs.existsSync(process.env.DF_ACTIVE_TEST))id=fs.readFileSync(process.env.DF_ACTIVE_TEST,'utf8').trim();trace.context.run(id,()=>fn(call,callback));};}
const server=new grpc.Server();server.addService(proto.CurrencyService.service,{getSupportedCurrencies:handler(context.getSupportedCurrencies),convert:handler(context.convert)});
server.bindAsync(`127.0.0.1:${process.env.DF_CURRENCY_PORT}`,grpc.ServerCredentials.createInsecure(),error=>{if(error)throw error;console.log('Original currency algorithms ready in test host');});
process.on('SIGTERM',()=>server.tryShutdown(()=>process.exit(0)));
