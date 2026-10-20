package dataflowtrace

import (
 "context"
 "encoding/json"
 "fmt"
 "os"
 "regexp"
 "strings"
 "sync"
 "google.golang.org/grpc"
 "google.golang.org/grpc/metadata"
)

var mu sync.Mutex
var serial sync.Mutex
var current string
var frames int
var pan=regexp.MustCompile(`\b[0-9]{12,19}\b`)
func write(value any){data,err:=json.Marshal(value);if err!=nil{panic(err)};file,err:=os.OpenFile(os.Getenv("DF_NATIVE_EVENTS"),os.O_CREATE|os.O_APPEND|os.O_WRONLY,0600);if err!=nil{panic(err)};defer file.Close();if _,err=file.Write(append(data,'\n'));err!=nil{panic(err)}}
func Enter(scope,node string)string{mu.Lock();frames++;frame:=fmt.Sprintf("go:%d",frames);mu.Unlock();Hit(frame,node,"node");Hit(frame,node,"def");return frame}
func Hit(frame,node,phase string){mu.Lock();defer mu.Unlock();write(map[string]any{"test_id":current,"frame":frame,"node":node,"phase":phase})}
func Predicate(frame,head,yes,no string,value bool)bool{Hit(frame,head,"node");chosen:=no;if value{chosen=yes};Hit(frame,chosen,"node");Hit(frame,chosen,"use");Hit(frame,chosen,"def");return value}
func Tool(name,args,result string,err error){mu.Lock();defer mu.Unlock();message:="";if err!=nil{message=err.Error()};write(map[string]any{"test_id":current,"phase":"checkpoint","step":"TOOL_DONE","data":map[string]any{"tool":name,"arguments":pan.ReplaceAllString(args,"[REDACTED_PAN]"),"result":pan.ReplaceAllString(result,"[REDACTED_PAN]"),"error":message}})}
func Unary(ctx context.Context,req any,info *grpc.UnaryServerInfo,handler grpc.UnaryHandler)(any,error){serial.Lock();defer serial.Unlock();id:="";if md,ok:=metadata.FromIncomingContext(ctx);ok{ids:=md.Get("x-dataflow-test-id");if len(ids)>0{id=ids[0]}};if id==""{data,_:=os.ReadFile(os.Getenv("DF_ACTIVE_TEST"));id=strings.TrimSpace(string(data))};mu.Lock();current=id;mu.Unlock();return handler(ctx,req)}
