// Test host replaces only server startup; CartServiceImpl and agent bodies are original.
using System.Net;
using Grpc.Core;
using Grpc.Core.Interceptors;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using cartservice;

var builder=WebApplication.CreateBuilder(args);
builder.Services.AddGrpc(options=>options.Interceptors.Add<Correlation>());
builder.Services.AddHttpClient();
builder.Services.AddSingleton<OllamaCartAgent>(services=>new OllamaCartAgent(
 new HttpClient{Timeout=TimeSpan.FromSeconds(180)},services.GetRequiredService<ILogger<OllamaCartAgent>>(),builder.Configuration));
builder.Services.AddSingleton<CartServiceImpl>();
builder.WebHost.ConfigureKestrel(options=>options.Listen(IPAddress.Loopback,
 int.Parse(Environment.GetEnvironmentVariable("DF_CART_PORT")??"50062"),listen=>listen.Protocols=HttpProtocols.Http2));
var app=builder.Build();app.MapGrpcService<CartServiceImpl>();app.Run();

sealed class Correlation:Interceptor {
 public override async Task<TResponse> UnaryServerHandler<TRequest,TResponse>(TRequest request,ServerCallContext context,UnaryServerMethod<TRequest,TResponse> continuation){
  DataflowTrace.TestId.Value=context.RequestHeaders.FirstOrDefault(e=>e.Key=="x-dataflow-test-id")?.Value;
  try{return await continuation(request,context);}finally{DataflowTrace.TestId.Value=null;}
 }
}
