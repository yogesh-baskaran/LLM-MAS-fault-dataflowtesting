"""Statement CFG and binding-level dataflow; never infer execution from labels.

Scope: selected Python functions and JavaScript charge(). Conservative exception
edges are candidates, not proofs of feasibility. Heap aliases, expression-internal
short circuit paths, comprehensions and called library bodies are outside scope.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


def names(node: ast.AST | None, context: type = ast.Load) -> set[str]:
    if node is None:
        return set()
    excluded = {n.id for c in ast.walk(node) if isinstance(c, ast.comprehension)
                for n in ast.walk(c.target) if isinstance(n, ast.Name)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
            and isinstance(n.ctx, context)} - excluded


class PythonCFG:
    def __init__(self, source: str, filename: str, function: ast.AST):
        self.source, self.filename, self.function = source, filename, function
        self.scope = filename + ':' + function.name
        parameters = [a.arg for a in function.args.posonlyargs + function.args.args + function.args.kwonlyargs]
        self.locals = set(parameters) | names(function, ast.Store)
        self.locals |= {n.name for n in ast.walk(function) if isinstance(n, ast.ExceptHandler) and n.name}
        self.locals -= {'self', 'lkw'}
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.stmt_ids: dict[int, str] = {}
        self.branches: dict[int, tuple[str, str]] = {}
        self.handlers: dict[int, str] = {}
        self.loop_stack = []
        self.counter = 0
        self.entry = self.node(function, 'entry', set(parameters) & self.locals)
        exits = self.block(function.body, [self.entry])
        self.exit = self.node(function, 'exit')
        self.connect(exits, self.exit)

    def node(self, statement, kind, defs=(), uses=()):
        self.counter += 1
        node_id = f'{self.scope}:{statement.lineno}:{self.counter}'
        self.nodes[node_id] = {
            'id': node_id, 'scope': self.scope, 'file': self.filename,
            'line': statement.lineno, 'kind': kind,
            'definitions': sorted(set(defs) & self.locals),
            'uses': sorted(set(uses) & self.locals),
            'use_kind': 'predicate' if kind in {'true', 'false'} else 'computational',
            'source': (ast.get_source_segment(self.source, statement) or '').split('\n')[0][:240],
        }
        return node_id

    def connect(self, previous, node):
        for item in previous:
            self.edges[item].add(node)

    def block(self, statements, previous):
        for statement in statements:
            previous = self.statement(statement, previous)
        return previous

    def statement(self, statement, previous):
        if isinstance(statement, (ast.AsyncFor, ast.AsyncWith, ast.Match)):
            raise ValueError(f'Unsupported CFG construct {type(statement).__name__} at {self.filename}:{statement.lineno}')
        if isinstance(statement, (ast.For, ast.While)):
            test = statement.iter if isinstance(statement, ast.For) else statement.test
            node=self.node(statement,'loop');self.stmt_ids[id(statement)]=node;self.connect(previous,node)
            yes=self.node(statement,'true',names(statement.target,ast.Store) if isinstance(statement,ast.For) else (),names(test))
            no=self.node(statement,'false',uses=names(test));self.branches[id(statement)]=yes,no
            self.connect([node],yes);self.connect([node],no)
            breaks=[];self.loop_stack.append((node,breaks))
            exits=self.block(statement.body,[yes]);self.loop_stack.pop();self.connect(exits,node)
            return self.block(statement.orelse,[no])+breaks
        if isinstance(statement, (ast.Break,ast.Continue)):
            node=self.node(statement,'statement');self.stmt_ids[id(statement)]=node;self.connect(previous,node)
            if not self.loop_stack:raise ValueError('Loop control outside supported loop')
            header,breaks=self.loop_stack[-1]
            if isinstance(statement,ast.Break):breaks.append(node)
            else:self.connect([node],header)
            return []
        if isinstance(statement,ast.With):
            uses=set().union(*(names(i.context_expr) for i in statement.items))
            defs=set().union(*(names(i.optional_vars,ast.Store) for i in statement.items))
            node=self.node(statement,'with',defs,uses);self.stmt_ids[id(statement)]=node;self.connect(previous,node)
            return self.block(statement.body,[node])
        if isinstance(statement, ast.Try):
            if statement.finalbody:
                raise ValueError('try/finally requires a more detailed CFG')
            node = self.node(statement, 'try')
            self.stmt_ids[id(statement)] = node
            self.connect(previous, node)
            before = set(self.nodes)
            exits = self.block(statement.body, [node])
            body_nodes = set(self.nodes) - before
            exits = self.block(statement.orelse, exits)
            for handler in statement.handlers:
                hnode = self.node(handler, 'except', [handler.name] if handler.name else [])
                self.handlers[id(handler)] = hnode
                # Overapproximation: a matching exception could exit any protected statement.
                for body in body_nodes:
                    if self.nodes[body]['kind'] not in {'true', 'false', 'return'}:
                        self.edges[body].add(hnode)
                exits += self.block(handler.body, [hnode])
            return exits
        if isinstance(statement, ast.If):
            node = self.node(statement, 'predicate')
            self.stmt_ids[id(statement)] = node
            self.connect(previous, node)
            true = self.node(statement, 'true', uses=names(statement.test))
            false = self.node(statement, 'false', uses=names(statement.test))
            self.branches[id(statement)] = true, false
            self.connect([node], true)
            self.connect([node], false)
            return self.block(statement.body, [true]) + self.block(statement.orelse, [false])
        defs = names(statement, ast.Store) if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)) else set()
        uses = names(statement)
        if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
            uses.add(statement.target.id)
        kind = 'return' if isinstance(statement, ast.Return) else 'statement'
        node = self.node(statement, kind, defs, uses)
        self.stmt_ids[id(statement)] = node
        self.connect(previous, node)
        return [] if isinstance(statement, (ast.Return, ast.Raise)) else [node]

    def catalog(self):
        return {'scope': self.scope, 'entry': self.entry, 'nodes': list(self.nodes.values()),
                'edges': sorted([[a, b] for a, targets in self.edges.items() for b in targets])}

    def instrument(self):
        def emit(node, phase):
            return ast.Expr(ast.Call(ast.Attribute(ast.Name('_df_recorder', ast.Load()), 'event', ast.Load()),
                                     [ast.Name('_df_frame', ast.Load()), ast.Constant(node), ast.Constant(phase)], []))

        def walk(statements):
            output = []
            for statement in statements:
                node = self.stmt_ids[id(statement)]
                if isinstance(statement,(ast.For,ast.While)):
                    yes,no=self.branches[id(statement)]
                    body=[emit(yes,'node'),emit(yes,'use'),emit(yes,'def')]+walk(statement.body)
                    if isinstance(statement,ast.For):
                        statement.iter=ast.Call(ast.Attribute(ast.Name('_df_recorder',ast.Load()),'iterate',ast.Load()),
                            [ast.Name('_df_frame',ast.Load()),ast.Constant(node),statement.iter],[])
                    else:
                        statement.test=ast.Call(ast.Attribute(ast.Name('_df_recorder',ast.Load()),'predicate',ast.Load()),
                            [ast.Name('_df_frame',ast.Load()),ast.Constant(node),statement.test],[])
                    statement.body=body
                    statement.orelse=[emit(no,'node'),emit(no,'use')]+walk(statement.orelse)
                    output.append(statement)
                    continue
                output += [emit(node, 'node')]
                if isinstance(statement, ast.If):
                    true, false = self.branches[id(statement)]
                    statement.body = [emit(true, 'node'), emit(true, 'use')] + walk(statement.body)
                    statement.orelse = [emit(false, 'node'), emit(false, 'use')] + walk(statement.orelse)
                    output.append(statement)
                elif isinstance(statement,ast.With):
                    output.append(emit(node,'use'))
                    statement.body=[emit(node,'def')]+walk(statement.body)
                    output.append(statement)
                elif isinstance(statement, ast.Try):
                    statement.body = walk(statement.body)
                    statement.orelse = walk(statement.orelse)
                    for handler in statement.handlers:
                        hnode = self.handlers[id(handler)]
                        handler.body = [emit(hnode, 'node'), emit(hnode, 'def')] + walk(handler.body)
                    output.append(statement)
                else:
                    if self.nodes[node]['uses']:
                        output.append(emit(node, 'use'))
                    output.append(statement)
                    if self.nodes[node]['definitions']:
                        output.append(emit(node, 'def'))
            return output

        self.function.body = walk(self.function.body)
        enter = ast.Assign([ast.Name('_df_frame', ast.Store())],
                           ast.Call(ast.Attribute(ast.Name('_df_recorder', ast.Load()), 'enter', ast.Load()),
                                    [ast.Constant(self.scope), ast.Constant(self.entry)], []))
        self.function.body.insert(0, enter)


def prepare_python(path: Path, filename: str, function_names: set[str]):
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source, filename=filename)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in function_names]
    if {f.name for f in functions} != function_names:
        raise ValueError(f'Missing target function in {path}')
    catalogs = []
    for function in functions:
        cfg = PythonCFG(source, filename, function)
        catalogs.append(cfg.catalog())
        cfg.instrument()
    return compile(ast.fix_missing_locations(tree), filename, 'exec'), catalogs


class Recorder:
    def __init__(self, catalogs):
        self.nodes = {n['id']: n for c in catalogs for n in c['nodes']}
        self.events = []
        self.frames = 0
        self.test_id = None
        self.checkpoints = defaultdict(list)

    def enter(self, scope, node):
        self.frames += 1
        frame = f'python:{self.frames}'
        self.event(frame, node, 'node')
        self.event(frame, node, 'def')
        return frame

    def event(self, frame, node, phase):
        self.events.append({'test_id': self.test_id, 'frame': frame, 'node': node, 'phase': phase})

    def iterate(self,frame,node,iterable):
        iterator=iter(iterable)
        while True:
            self.event(frame,node,'node')
            try:value=next(iterator)
            except StopIteration:return
            yield value

    def predicate(self,frame,node,value):
        self.event(frame,node,'node')
        return value


def enumerate_obligations(catalogs, max_paths=50000, max_node_visits=3):
    """Enumerate definition-clear paths within an explicit node-visit bound.

    A definition at a statement kills the incoming binding only after RHS uses.
    Predicate uses are attached to true/false edges, represented as explicit nodes.
    Observed paths beyond this bound are reported separately by build_coverage.
    """
    obligations, definitions, truncated = [], [], False
    for catalog in catalogs:
        nodes = {n['id']: n for n in catalog['nodes']}
        edges = defaultdict(list)
        for a, b in catalog['edges']:
            edges[a].append(b)
        for start, definition in nodes.items():
            for variable in definition['definitions']:
                key = f'{start}|{variable}'
                definitions.append({'id': key, 'node': start, 'variable': variable, 'scope': catalog['scope']})
                stack = [(n, [start]) for n in edges[start]]
                while stack:
                    current, prefix = stack.pop()
                    if prefix.count(current)>=max_node_visits:
                        continue
                    path = prefix + [current]
                    node = nodes[current]
                    if variable in node['uses']:
                        obligations.append({'id': f'p{len(obligations)+1}', 'definition': key,
                                            'use': f'{current}|{variable}', 'variable': variable,
                                            'use_kind': node['use_kind'], 'scope': catalog['scope'],
                                            'path': path, 'feasibility': 'unknown'})
                        if len(obligations) >= max_paths:
                            truncated = True
                            break
                    if variable not in node['definitions']:
                        stack.extend((n, path) for n in edges[current])
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break
    return definitions, obligations, truncated


def build_coverage(catalogs, events, max_paths=50000, max_node_visits=3):
    nodes = {n['id']: n for c in catalogs for n in c['nodes']}
    _, obligations, truncated = enumerate_obligations(catalogs, max_paths,max_node_visits)
    # Definitions and reachable endpoint pairs must remain complete even when
    # bounded path enumeration hits its cap. Compute these independently.
    definitions=[];static_pairs={}
    for catalog in catalogs:
        local_nodes={n['id']:n for n in catalog['nodes']};edges=defaultdict(list)
        for a,b in catalog['edges']:edges[a].append(b)
        for start,definition in local_nodes.items():
            for variable in definition['definitions']:
                key=f'{start}|{variable}'
                definitions.append({'id':key,'node':start,'variable':variable,'scope':catalog['scope']})
                pending=list(edges[start]);seen=set()
                while pending:
                    current=pending.pop()
                    if current in seen:continue
                    seen.add(current);node=local_nodes[current]
                    if variable in node['uses']:
                        use=f'{current}|{variable}'
                        static_pairs[(key,use)]={'definition':key,'use':use,'variable':variable,'use_kind':node['use_kind'],'scope':catalog['scope']}
                    if variable not in node['definitions']:pending.extend(edges[current])
    frames = defaultdict(list)
    for event in events:
        if event['node'] in nodes:
            frames[(event['test_id'], event['frame'])].append(event)
    hits = defaultdict(set)
    observed_definitions = defaultdict(set)
    observed_nodes = defaultdict(set)
    pair_hits = defaultdict(set)
    dynamic_paths = defaultdict(set)
    for (test_id, _), frame in frames.items():
        last_defs = {}
        path = []
        for event in frame:
            node = nodes[event['node']]
            if event['phase'] == 'node':
                path.append(event['node'])
                observed_nodes[event['node']].add(test_id)
            elif event['phase'] == 'def':
                for variable in node['definitions']:
                    key = f"{event['node']}|{variable}"
                    observed_definitions[key].add(test_id)
                    last_defs[variable] = (key, len(path)-1)
            elif event['phase'] == 'use':
                for variable in node['uses']:
                    if variable in last_defs:
                        definition, offset = last_defs[variable]
                        use = f"{event['node']}|{variable}"
                        pair_hits[(definition, use)].add(test_id)
                        dynamic_paths[(definition, use, tuple(path[offset:]))].add(test_id)
    for obligation in obligations:
        evidence = dynamic_paths.get((obligation['definition'], obligation['use'], tuple(obligation['path'])), set())
        obligation['tests'] = sorted(evidence)
        if evidence:
            obligation['feasibility'] = 'observed_feasible'
            hits[obligation['definition']].update(evidence)
    pairs = static_pairs
    for index,(key,item) in enumerate(pairs.items(),1):
        item['tests']=sorted(pair_hits.get(key,set()));item['id']=f'u{index}'
    for item in definitions:
        item['executed_by'] = sorted(observed_definitions.get(item['id'], set()))
        # All-definitions requires a reached use, not merely assignment execution.
        item['tests'] = sorted({test for (definition, _), tests in pair_hits.items()
                                if definition == item['id'] for test in tests})
        item['candidate_use_count'] = sum(1 for p in pairs if p[0] == item['id'])
    def metric(items):
        total = len(items)
        covered = sum(bool(item['tests']) for item in items)
        return {'covered': covered, 'total_candidates': total,
                'candidate_percent': round(100*covered/total, 2) if total else None,
                'uncovered_candidates': total-covered}
    mismatches = []
    expected = {(p['definition'], p['use'], tuple(p['path'])) for p in obligations}
    if not truncated:
        mismatches = [{'definition': d, 'use': u, 'path': list(p)}
                      for d, u, p in dynamic_paths if (d, u, p) not in expected and max((p.count(n) for n in set(p)),default=0)<=max_node_visits]
    outside_bounds=sum(1 for _,_,p in dynamic_paths if max((p.count(n) for n in set(p)),default=0)>max_node_visits)
    return {'schema_version': 1, 'scope': 'intraprocedural local bindings; statement CFG',
            'all_definitions': metric(definitions), 'all_uses': metric(list(pairs.values())),
            'all_du_paths': metric(obligations), 'path_enumeration_truncated': truncated,
            'observed_paths_missing_from_cfg': mismatches,
            'complete_feasible_coverage_claim': False,
            'max_node_visits':max_node_visits,'observed_du_paths_outside_bound':outside_bounds,
            'limitations': ['Uncovered CFG candidates have unknown feasibility; no automated infeasibility proof.',
                           'Fault-only branches remain uncovered because FAULT_MODE=NONE.',
                           'Heap field writes, alias analysis, library internals, comprehensions, expression-internal short circuits and ternary paths are outside this binding-level scope.',
                           'Exception edges are conservative; full feasible all-DU-path coverage is not certified.'],
            'definitions': definitions, 'uses': list(pairs.values()), 'du_paths': obligations,
            'catalogs': catalogs, 'node_hits': {n: sorted(t) for n, t in observed_nodes.items()}}
