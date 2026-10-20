using System.Text.Json;
using System.Threading;

public static class DataflowTrace {
 static readonly object Gate=new();static long sequence;
 public static readonly AsyncLocal<string?> TestId=new();
 public static string Enter(string scope,string node){var frame="csharp:"+Interlocked.Increment(ref sequence);Hit(frame,node,"node");Hit(frame,node,"def");return frame;}
 public static void Hit(string frame,string node,string phase){var test=TestId.Value;if(string.IsNullOrWhiteSpace(test)){var path=Environment.GetEnvironmentVariable("DF_ACTIVE_TEST");test=path is not null&&File.Exists(path)?File.ReadAllText(path).Trim():"uncorrelated";}
  lock(Gate){File.AppendAllText(Environment.GetEnvironmentVariable("DF_NATIVE_EVENTS")!,JsonSerializer.Serialize(new{test_id=test,frame,node,phase})+"\n");}}
}
