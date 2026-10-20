// Roslyn instrumentation of selected methods in test copies; originals stay read-only.
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using static Microsoft.CodeAnalysis.CSharp.SyntaxFactory;

if(args.Length!=4)throw new ArgumentException("ORIGINAL OUTPUT RELATIVE_FILE comma,separated,methods");
if(Path.GetFullPath(args[0])==Path.GetFullPath(args[1]))throw new ArgumentException("Output must be a separate test copy");
var tree=CSharpSyntaxTree.ParseText(File.ReadAllText(args[0]));
var rewrite=new Instrument(args[2],args[3].Split(',').ToHashSet());
var root=rewrite.Visit(tree.GetRoot())!;
File.WriteAllText(args[1],root.NormalizeWhitespace().ToFullString());
File.WriteAllText(args[1]+".catalog.json",JsonSerializer.Serialize(rewrite.Catalogs,new JsonSerializerOptions{WriteIndented=true}));

sealed class Instrument(string filename,HashSet<string> methods):CSharpSyntaxRewriter {
 public List<object> Catalogs=new();
 List<Dictionary<string,object>> nodes=new();List<string[]> edges=new();HashSet<string> locals=new();string scope="";
 Dictionary<StatementSyntax,string> ids=new();
 public override SyntaxNode? VisitMethodDeclaration(MethodDeclarationSyntax method){
  if(!methods.Contains(method.Identifier.Text)||method.Body is null)return method;
  nodes=new();edges=new();ids=new();scope=filename+":"+method.Identifier.Text;
  locals=method.ParameterList.Parameters.Select(p=>p.Identifier.Text)
   .Concat(method.Body.DescendantNodes().OfType<VariableDeclaratorSyntax>().Select(v=>v.Identifier.Text))
   .Concat(method.Body.DescendantNodes().OfType<SingleVariableDesignationSyntax>().Select(v=>v.Identifier.Text))
   .Concat(method.Body.DescendantNodes().OfType<CatchDeclarationSyntax>().Select(v=>v.Identifier.Text))
   .Concat(method.Body.DescendantNodes().OfType<ForEachStatementSyntax>().Select(v=>v.Identifier.Text)).Where(n=>n!="_").ToHashSet();
  var entry=Node(method,"entry",method.ParameterList.Parameters.Select(p=>p.Identifier.Text),Array.Empty<string>());
  var (body,exits)=Block(method.Body.Statements,new(){entry});
  var begin=ParseStatement($"var _dfFrame = global::DataflowTrace.Enter({Q(scope)},{Q(entry)});");
  Catalogs.Add(new{scope,entry,nodes=nodes.ToArray(),edges=edges.ToArray()});
  return method.WithBody(method.Body.WithStatements(List(new[]{begin}.Concat(body))));
 }
 string Q(string value)=>JsonSerializer.Serialize(value);
 IEnumerable<string> Uses(SyntaxNode? n)=>n is null?Array.Empty<string>():n.DescendantNodesAndSelf().OfType<IdentifierNameSyntax>()
  .Where(i=>locals.Contains(i.Identifier.Text)&&!(i.Parent is MemberAccessExpressionSyntax member&&member.Name==i))
  .Select(i=>i.Identifier.Text).Distinct();
 IEnumerable<string> Defs(StatementSyntax s){
  if(s is LocalDeclarationStatementSyntax d)return d.Declaration.Variables.Select(v=>v.Identifier.Text);
  if(s is ExpressionStatementSyntax e&&e.Expression is AssignmentExpressionSyntax a&&a.Left is IdentifierNameSyntax id)return new[]{id.Identifier.Text};
  return Array.Empty<string>();
 }
 IEnumerable<string> Rhs(StatementSyntax s){
  if(s is LocalDeclarationStatementSyntax d)return d.Declaration.Variables.SelectMany(v=>Uses(v.Initializer?.Value));
  if(s is ExpressionStatementSyntax e&&e.Expression is AssignmentExpressionSyntax a&&a.Left is IdentifierNameSyntax)return Uses(a.Right);
  return Uses(s);
 }
 string Node(SyntaxNode n,string kind,IEnumerable<string> defs,IEnumerable<string> uses){
  int line=n.GetLocation().GetLineSpan().StartLinePosition.Line+1;var id=$"{scope}:{line}:{nodes.Count+1}";
  nodes.Add(new(){["id"]=id,["scope"]=scope,["file"]=filename,["line"]=line,["kind"]=kind,
   ["definitions"]=defs.Where(locals.Contains).Distinct().ToArray(),["uses"]=uses.Where(locals.Contains).Distinct().ToArray(),
   ["use_kind"]=kind is "true" or "false"?"predicate":"computational",["source"]=n.ToString().Split('\n')[0]});return id;
 }
 void Connect(IEnumerable<string> previous,string next){foreach(var p in previous)edges.Add(new[]{p,next});}
 StatementSyntax Hit(string id,string phase)=>ParseStatement($"global::DataflowTrace.Hit(_dfFrame,{Q(id)},{Q(phase)});");
 IEnumerable<StatementSyntax> AsBody(StatementSyntax s)=>s is BlockSyntax b?b.Statements:new[]{s};
 (List<StatementSyntax>,List<string>) Block(IEnumerable<StatementSyntax> statements,List<string> previous){var output=new List<StatementSyntax>();foreach(var s in statements){var (part,exits)=Stmt(s,previous);output.AddRange(part);previous=exits;}return(output,previous);}
 (List<StatementSyntax>,List<string>) Stmt(StatementSyntax s,List<string> previous){
  if(s is BlockSyntax block){var(body,end)=Block(block.Statements,previous);return(new(){block.WithStatements(List(body))},end);}
  if(s is IfStatementSyntax condition){
   var head=Node(s,"predicate",Array.Empty<string>(),Array.Empty<string>());Connect(previous,head);
   var outdefs=condition.Condition.DescendantNodes().OfType<SingleVariableDesignationSyntax>().Select(v=>v.Identifier.Text).ToArray();
   var yes=Node(s,"true",outdefs,Uses(condition.Condition));var no=Node(s,"false",outdefs,Uses(condition.Condition));Connect(new[]{head},yes);Connect(new[]{head},no);
   var (body,a)=Block(AsBody(condition.Statement),new(){yes});var(other,z)=condition.Else is null?(new List<StatementSyntax>(),new List<string>{no}):Block(AsBody(condition.Else.Statement),new(){no});
   body.InsertRange(0,new[]{Hit(yes,"node"),Hit(yes,"use"),Hit(yes,"def")});other.InsertRange(0,new[]{Hit(no,"node"),Hit(no,"use"),Hit(no,"def")});
   return(new(){Hit(head,"node"),condition.WithStatement(SyntaxFactory.Block(body)).WithElse(ElseClause(SyntaxFactory.Block(other)))},a.Concat(z).ToList());
  }
  if(s is CommonForEachStatementSyntax loop){
   var expression=loop.Expression;var defs=s is ForEachStatementSyntax simple?new[]{simple.Identifier.Text}:s.DescendantNodes().OfType<ParenthesizedVariableDesignationSyntax>().SelectMany(v=>v.Variables.OfType<SingleVariableDesignationSyntax>().Select(x=>x.Identifier.Text)).Distinct().ToArray();
   var head=Node(s,"loop",Array.Empty<string>(),Array.Empty<string>());Connect(previous,head);var yes=Node(s,"true",defs,Uses(expression));var no=Node(s,"false",Array.Empty<string>(),Uses(expression));Connect(new[]{head},yes);Connect(new[]{head},no);
   var(body,end)=Block(AsBody(loop.Statement),new(){yes});Connect(end,head);body.InsertRange(0,new[]{Hit(head,"node"),Hit(yes,"node"),Hit(yes,"use"),Hit(yes,"def")});
   var changed=loop.WithStatement(SyntaxFactory.Block(body));return(new(){changed,Hit(head,"node"),Hit(no,"node"),Hit(no,"use")},new(){no});
  }
  if(s is TryStatementSyntax attempt){
   if(attempt.Finally is not null)throw new NotSupportedException("finally requires an extended CFG");
   var head=Node(s,"try",Array.Empty<string>(),Array.Empty<string>());Connect(previous,head);int start=nodes.Count;var(body,end)=Block(attempt.Block.Statements,new(){head});
   var protectedIds=nodes.Skip(start).Where(n=>(string)n["kind"]!="return").Select(n=>(string)n["id"]).ToArray();var catches=new List<CatchClauseSyntax>();
   foreach(var catcher in attempt.Catches){var name=catcher.Declaration?.Identifier.Text;var id=Node(catcher,"except",name is null?Array.Empty<string>():new[]{name},Array.Empty<string>());foreach(var p in protectedIds)Connect(new[]{p},id);var(cb,ce)=Block(catcher.Block.Statements,new(){id});cb.InsertRange(0,new[]{Hit(id,"node"),Hit(id,"def")});catches.Add(catcher.WithBlock(SyntaxFactory.Block(cb)));end.AddRange(ce);}
   return(new(){Hit(head,"node"),attempt.WithBlock(SyntaxFactory.Block(body)).WithCatches(List(catches))},end);
  }
  if(s is not(LocalDeclarationStatementSyntax or ExpressionStatementSyntax or ReturnStatementSyntax or ThrowStatementSyntax or EmptyStatementSyntax))throw new NotSupportedException($"Unsupported C# statement {s.Kind()} in {scope}");
  var kind=s is ReturnStatementSyntax or ThrowStatementSyntax?"return":"statement";var node=Node(s,kind,Defs(s),Rhs(s));Connect(previous,node);var output=new List<StatementSyntax>{Hit(node,"node"),Hit(node,"use"),s};if(kind=="return")return(output,new());output.Add(Hit(node,"def"));return(output,new(){node});
 }
}
