// Instrument test copies using Go's parser/AST. No original source is written.
package main

import (
 "bytes"
 "encoding/json"
 "fmt"
 "go/ast"
 "go/format"
 "go/parser"
 "go/token"
 "os"
 "strconv"
 "strings"
)

type Node struct { ID string `json:"id"`; Scope string `json:"scope"`; File string `json:"file"`; Line int `json:"line"`; Kind string `json:"kind"`; Defs []string `json:"definitions"`; Uses []string `json:"uses"`; UseKind string `json:"use_kind"`; Source string `json:"source"` }
type Catalog struct { Scope string `json:"scope"`; Entry string `json:"entry"`; Nodes []Node `json:"nodes"`; Edges [][2]string `json:"edges"` }
type Loop struct { next string; breaks []string }
type Builder struct { fs *token.FileSet; file,scope string; locals map[*ast.Object]string; catalog Catalog; loops []*Loop }
func expr(s string) ast.Expr { e,err:=parser.ParseExpr(s);if err!=nil{panic(err)};return e }
func statement(s string) ast.Stmt { f,err:=parser.ParseFile(token.NewFileSet(),"generated.go","package p\nfunc f(){"+s+"}",0);if err!=nil{panic(err)};return f.Decls[0].(*ast.FuncDecl).Body.List[0] }
func (b *Builder) key(i *ast.Ident) string { if i==nil || i.Name=="_" || i.Obj==nil{return ""};return b.locals[i.Obj] }
func unique(values []string) []string { out:=[]string{};seen:=map[string]bool{};for _,v:=range values{if v!=""&&!seen[v]{out=append(out,v);seen[v]=true}};return out }
func (b *Builder) uses(n ast.Node) []string { out:=[]string{};if n!=nil {ast.Inspect(n,func(n ast.Node)bool{if i,ok:=n.(*ast.Ident);ok{out=append(out,b.key(i))};return true})};return unique(out) }
func (b *Builder) definitions(s ast.Stmt) []string { out:=[]string{};switch n:=s.(type){case *ast.AssignStmt:for _,x:=range n.Lhs{if i,ok:=x.(*ast.Ident);ok{out=append(out,b.key(i))}};case *ast.DeclStmt:if d,ok:=n.Decl.(*ast.GenDecl);ok{for _,spec:=range d.Specs{if v,ok:=spec.(*ast.ValueSpec);ok{for _,i:=range v.Names{out=append(out,b.key(i))}}}};case *ast.IncDecStmt:if i,ok:=n.X.(*ast.Ident);ok{out=append(out,b.key(i))}};return unique(out) }
func (b *Builder) rhs(s ast.Stmt) []string { out:=[]string{};switch n:=s.(type){case *ast.AssignStmt:for _,x:=range n.Rhs{out=append(out,b.uses(x)...)};for _,x:=range n.Lhs{if _,ok:=x.(*ast.Ident);!ok{out=append(out,b.uses(x)...)}else if n.Tok!=token.ASSIGN&&n.Tok!=token.DEFINE{out=append(out,b.uses(x)...)}};case *ast.DeclStmt:if d,ok:=n.Decl.(*ast.GenDecl);ok{for _,spec:=range d.Specs{if v,ok:=spec.(*ast.ValueSpec);ok{for _,x:=range v.Values{out=append(out,b.uses(x)...)}}}};default:out=b.uses(s)};return unique(out) }
func (b *Builder) node(n ast.Node,kind string,defs,uses []string) string { line:=b.fs.Position(n.Pos()).Line;id:=fmt.Sprintf("%s:%d:%d",b.scope,line,len(b.catalog.Nodes)+1);var src bytes.Buffer;format.Node(&src,b.fs,n);usekind:="computational";if kind=="true"||kind=="false"{usekind="predicate"};b.catalog.Nodes=append(b.catalog.Nodes,Node{id,b.scope,b.file,line,kind,unique(defs),unique(uses),usekind,strings.Split(src.String(),"\n")[0]});return id }
func (b *Builder) connect(previous []string,next string){for _,p:=range previous{b.catalog.Edges=append(b.catalog.Edges,[2]string{p,next})}}
func hit(id,phase string) ast.Stmt{return statement(fmt.Sprintf("df.Hit(_dfFrame,%q,%q)",id,phase))}
func (b *Builder) block(body []ast.Stmt,previous []string)([]ast.Stmt,[]string){out:=[]ast.Stmt{};for _,s:=range body{part,exits:=b.stmt(s,previous);out=append(out,part...);previous=exits};return out,previous}
func (b *Builder) stmt(s ast.Stmt,previous []string)([]ast.Stmt,[]string){
 switch n:=s.(type){
 case *ast.BlockStmt: body,exits:=b.block(n.List,previous);n.List=body;return []ast.Stmt{n},exits
 case *ast.IfStmt:
  prefix:=[]ast.Stmt{};if n.Init!=nil{var exits []string;prefix,exits=b.stmt(n.Init,previous);previous=exits;n.Init=nil}
  head:=b.node(n,"predicate",nil,nil);b.connect(previous,head);yes:=b.node(n,"true",nil,b.uses(n.Cond));no:=b.node(n,"false",nil,b.uses(n.Cond));b.connect([]string{head},yes);b.connect([]string{head},no)
  condition:=n.Cond;n.Cond=expr(fmt.Sprintf("df.Predicate(_dfFrame,%q,%q,%q,%s)",head,yes,no,render(condition)))
  body,a:=b.block(n.Body.List,[]string{yes});n.Body.List=body
  other:=[]ast.Stmt{};z:=[]string{no};if n.Else!=nil{other,z=b.stmt(n.Else,[]string{no})};n.Else=&ast.BlockStmt{List:other}
  prefix=append(prefix,n);return []ast.Stmt{&ast.BlockStmt{List:prefix}},append(a,z...)
 case *ast.ForStmt:
  prefix:=[]ast.Stmt{};if n.Init!=nil{var exits []string;prefix,exits=b.stmt(n.Init,previous);previous=exits;n.Init=nil}
  head:=b.node(n,"loop",nil,nil);b.connect(previous,head);uses:=b.uses(n.Cond);yes:=b.node(n,"true",nil,uses);no:=b.node(n,"false",nil,uses);b.connect([]string{head},yes);b.connect([]string{head},no)
  cond:="true";if n.Cond!=nil{cond=render(n.Cond)};n.Cond=expr(fmt.Sprintf("df.Predicate(_dfFrame,%q,%q,%q,%s)",head,yes,no,cond))
  post:=[]ast.Stmt{};postEntry:=head;if n.Post!=nil{postEntry=b.node(n.Post,"statement",b.definitions(n.Post),b.rhs(n.Post));post=[]ast.Stmt{hit(postEntry,"node"),hit(postEntry,"use"),n.Post,hit(postEntry,"def")};b.connect([]string{postEntry},head);n.Post=&ast.ExprStmt{X:&ast.CallExpr{Fun:&ast.FuncLit{Type:&ast.FuncType{Params:&ast.FieldList{}},Body:&ast.BlockStmt{List:post}}}}}
  loop:=&Loop{next:postEntry};b.loops=append(b.loops,loop);body,exits:=b.block(n.Body.List,[]string{yes});b.loops=b.loops[:len(b.loops)-1];b.connect(exits,postEntry);n.Body.List=body;prefix=append(prefix,n);return []ast.Stmt{&ast.BlockStmt{List:prefix}},append([]string{no},loop.breaks...)
 case *ast.RangeStmt:
  head:=b.node(n,"loop",nil,nil);b.connect(previous,head);defs:=[]string{};for _,x:=range []ast.Expr{n.Key,n.Value}{if i,ok:=x.(*ast.Ident);ok{defs=append(defs,b.key(i))}};yes:=b.node(n,"true",defs,b.uses(n.X));no:=b.node(n,"false",nil,b.uses(n.X));b.connect([]string{head},yes);b.connect([]string{head},no)
  loop:=&Loop{next:head};b.loops=append(b.loops,loop);body,exits:=b.block(n.Body.List,[]string{yes});b.loops=b.loops[:len(b.loops)-1];b.connect(exits,head)
  n.Body.List=append([]ast.Stmt{hit(head,"node"),hit(yes,"node"),hit(yes,"use"),hit(yes,"def")},body...)
  return []ast.Stmt{n,hit(head,"node"),hit(no,"node"),hit(no,"use")},append([]string{no},loop.breaks...)
 case *ast.BranchStmt:
  id:=b.node(n,"statement",nil,nil);b.connect(previous,id);if len(b.loops)==0{panic("Unsupported branch outside loop")};loop:=b.loops[len(b.loops)-1];if n.Tok==token.CONTINUE{b.connect([]string{id},loop.next)}else if n.Tok==token.BREAK{loop.breaks=append(loop.breaks,id)}else{panic("Unsupported branch")};return []ast.Stmt{hit(id,"node"),n},nil
 case *ast.AssignStmt,*ast.DeclStmt,*ast.ExprStmt,*ast.ReturnStmt,*ast.DeferStmt,*ast.IncDecStmt,*ast.EmptyStmt:
  kind:="statement";if _,ok:=s.(*ast.ReturnStmt);ok{kind="return"};id:=b.node(s,kind,b.definitions(s),b.rhs(s));b.connect(previous,id);out:=[]ast.Stmt{hit(id,"node"),hit(id,"use"),s};if kind=="return"{return out,nil};out=append(out,hit(id,"def"))
  if assignment,ok:=s.(*ast.AssignStmt);ok && strings.Contains(render(assignment),"tool.Execute(ctx, args)"){out=append(out,statement("df.Tool(toolName,args,result,err)"))};return out,[]string{id}
 default:panic(fmt.Sprintf("Unsupported Go statement %T in %s",s,b.scope))
 }
}
func render(n ast.Node) string{var b bytes.Buffer;format.Node(&b,token.NewFileSet(),n);return b.String()}
func main(){
 if len(os.Args)>1&&os.Args[1]=="--"{os.Args=append(os.Args[:1],os.Args[2:]...)}
 if len(os.Args)!=5{panic("instrument ORIGINAL OUTPUT RELATIVE_FILE comma,separated,functions")};input,output,relative:=os.Args[1],os.Args[2],os.Args[3];wanted:=","+os.Args[4]+",";fs:=token.NewFileSet();file,err:=parser.ParseFile(fs,input,nil,parser.ParseComments);if err!=nil{panic(err)};catalogs:=[]Catalog{};instrumented:=false
 for _,declaration:=range file.Decls{fn,ok:=declaration.(*ast.FuncDecl);if !ok||fn.Body==nil||!strings.Contains(wanted,","+fn.Name.Name+","){continue};scope:=relative+":"+fn.Name.Name;if fn.Recv!=nil{scope=relative+":"+render(fn.Recv.List[0].Type)+"."+fn.Name.Name};b:=&Builder{fs:fs,file:relative,scope:scope,locals:map[*ast.Object]string{}}
  add:=func(i *ast.Ident){if i!=nil&&i.Obj!=nil&&i.Name!="_"{b.locals[i.Obj]=fmt.Sprintf("%s@%d",i.Name,fs.Position(i.Obj.Pos()).Line)}}
  params:=[]string{};for _,fields:=range []*ast.FieldList{fn.Recv,fn.Type.Params,fn.Type.Results}{if fields!=nil{for _,field:=range fields.List{for _,i:=range field.Names{add(i);params=append(params,b.key(i))}}}}
  ast.Inspect(fn.Body,func(n ast.Node)bool{switch x:=n.(type){case *ast.AssignStmt:for _,lhs:=range x.Lhs{if i,ok:=lhs.(*ast.Ident);ok{add(i)}};case *ast.ValueSpec:for _,i:=range x.Names{add(i)};case *ast.RangeStmt:if i,ok:=x.Key.(*ast.Ident);ok{add(i)};if i,ok:=x.Value.(*ast.Ident);ok{add(i)}};return true})
  b.catalog.Scope=scope;b.catalog.Entry=b.node(fn,"entry",params,nil);body,_:=b.block(fn.Body.List,[]string{b.catalog.Entry});fn.Body.List=append([]ast.Stmt{statement(fmt.Sprintf("_dfFrame := df.Enter(%q,%q)",scope,b.catalog.Entry))},body...);catalogs=append(catalogs,b.catalog);instrumented=true
 }
 if !instrumented{panic("No selected Go function found")}
 imp:=&ast.ImportSpec{Name:ast.NewIdent("df"),Path:&ast.BasicLit{Kind:token.STRING,Value:strconv.Quote("checkoutservice-agent/dataflowtrace")}};file.Decls=append([]ast.Decl{&ast.GenDecl{Tok:token.IMPORT,Specs:[]ast.Spec{imp}}},file.Decls...)
 var result bytes.Buffer;if err:=format.Node(&result,fs,file);err!=nil{panic(err)};if err:=os.WriteFile(output,result.Bytes(),0600);err!=nil{panic(err)};data,_:=json.MarshalIndent(catalogs,"","  ");if err:=os.WriteFile(output+".catalog.json",data,0600);err!=nil{panic(err)}
}
