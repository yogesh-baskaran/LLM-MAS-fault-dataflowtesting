/* Generate an instrumented TEST COPY. Never write into the original repository. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const parser = require('@babel/parser');
const traverse = require('@babel/traverse').default;
const generate = require('@babel/generator').default;
const t = require('@babel/types');

const [sourceFile, outputDir] = process.argv.slice(2);
if (!sourceFile || !outputDir) throw new Error('Usage: node instrument.cjs ORIGINAL/charge.js OUTPUT');
const source = fs.readFileSync(sourceFile, 'utf8');
const tree = parser.parse(source, {sourceType: 'script'});
let target;
traverse(tree, {FunctionExpression(p) {if (p.node.id?.name === 'charge') target = p;}});
if (!target) throw new Error('Expected module.exports = function charge(request)');
const filename = 'src/paymentservice/charge.js';
const scope = `${filename}:charge`;
const locals = new Set(Object.keys(target.scope.bindings));
const nodes = [], edges = [], statementIds = new Map(), branches = new Map();
let counter = 0;
function references(expression) {
  const result = new Set();
  if (!expression) return [];
  // Babel reference classification excludes property keys and declaration targets.
  target.traverse({Identifier(p) {
    if (p.node.start >= expression.start && p.node.end <= expression.end &&
        p.isReferencedIdentifier() && locals.has(p.node.name)) result.add(p.node.name);
  }});
  return [...result].sort();
}
function node(statement, kind, definitions = [], uses = []) {
  const id = `${scope}:${statement.loc.start.line}:${++counter}`;
  nodes.push({id, scope, file: filename, line: statement.loc.start.line, kind,
    definitions: definitions.filter(v => locals.has(v)).sort(), uses,
    use_kind: ['true','false'].includes(kind) ? 'predicate' : 'computational',
    source: source.slice(statement.start, statement.end).split('\n')[0].slice(0,240)});
  return id;
}
function connect(previous, next) {previous.forEach(p => edges.push([p,next]));}
function statements(body) {return t.isBlockStatement(body) ? body.body : [body];}
function block(body, previous) {
  for (const statement of body) {
    if (t.isIfStatement(statement)) {
      const id = node(statement, 'predicate'); statementIds.set(statement,id); connect(previous,id);
      const yes = node(statement,'true',[],references(statement.test));
      const no = node(statement,'false',[],references(statement.test));
      branches.set(statement,[yes,no]); connect([id],yes); connect([id],no);
      previous = [...block(statements(statement.consequent),[yes]),
        ...(statement.alternate ? block(statements(statement.alternate),[no]) : [no])];
    } else {
      if (!['VariableDeclaration','ExpressionStatement','ReturnStatement','ThrowStatement'].includes(statement.type))
        throw new Error(`Unsupported charge CFG statement ${statement.type}`);
      let defs = [];
      if (t.isVariableDeclaration(statement)) defs = statement.declarations.flatMap(d => Object.keys(t.getBindingIdentifiers(d.id)));
      const id = node(statement,t.isReturnStatement(statement) ? 'return' : 'statement',defs,references(statement));
      statementIds.set(statement,id); connect(previous,id);
      previous = t.isReturnStatement(statement) || t.isThrowStatement(statement) ? [] : [id];
    }
  }
  return previous;
}
const entry = node(target.node,'entry',Object.keys(t.getBindingIdentifiers(target.node.params[0])));
const exits = block(target.node.body.body,[entry]);
const exit = node(target.node,'exit'); connect(exits,exit);
function emit(id,phase) {
  return t.expressionStatement(t.callExpression(t.memberExpression(t.identifier('_dfRecorder'),t.identifier('event')),
    [t.identifier('_dfFrame'),t.stringLiteral(id),t.stringLiteral(phase)]));
}
function instrument(body) {
  const out = [];
  for (const statement of body) {
    const id = statementIds.get(statement); out.push(emit(id,'node'));
    if (t.isIfStatement(statement)) {
      const [yes,no] = branches.get(statement);
      statement.consequent = t.blockStatement([emit(yes,'node'),emit(yes,'use'),...instrument(statements(statement.consequent))]);
      statement.alternate = t.blockStatement([emit(no,'node'),emit(no,'use'),
        ...(statement.alternate ? instrument(statements(statement.alternate)) : [])]);
      out.push(statement);
    } else {
      const info = nodes.find(n => n.id === id);
      if (info.uses.length) out.push(emit(id,'use'));
      out.push(statement);
      if (info.definitions.length) out.push(emit(id,'def'));
    }
  }
  return out;
}
target.node.body.body = [t.variableDeclaration('const',[
  t.variableDeclarator(t.identifier('_dfFrame'),t.callExpression(t.memberExpression(t.identifier('_dfRecorder'),t.identifier('enter')),
    [t.stringLiteral(scope),t.stringLiteral(entry)]))]), ...instrument(target.node.body.body)];
tree.program.body.unshift(t.variableDeclaration('const',[t.variableDeclarator(t.identifier('_dfRecorder'),
  t.callExpression(t.identifier('require'),[t.stringLiteral('./trace.cjs')]))]));
fs.mkdirSync(outputDir,{recursive:true});
fs.writeFileSync(path.join(outputDir,'charge.instrumented.cjs'),generate(tree,{comments:true}).code);
fs.writeFileSync(path.join(outputDir,'service-catalog.json'),JSON.stringify({scope,entry,nodes,edges,
  source_sha256:crypto.createHash('sha256').update(source).digest('hex')},null,2));
fs.copyFileSync(path.join(__dirname,'trace.cjs'),path.join(outputDir,'trace.cjs'));
fs.copyFileSync(path.join(__dirname,'server.cjs'),path.join(outputDir,'server.cjs'));

