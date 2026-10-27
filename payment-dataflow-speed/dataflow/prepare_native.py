"""Build native test copies on SPEED. Original source trees are read-only inputs."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from dataflow.common import sha256


def adapt_checkout_startup(text):
    text,count=re.subn(r'const\s*\(\s*listenPort\s*=\s*"5050"\s*\)',
                       'var listenPort = os.Getenv("DF_CHECKOUT_PORT")\n\n',text)
    if count!=1:raise ValueError('Checkout startup changed: review test-host port adaptation')
    text,count=re.subn(r'(mongoURI := mustGetEnv\(\s*"MONGO_URI",\s*)"[^"\n]*"',
                       r'\1"mongodb://127.0.0.1:27027"',text)
    if count!=1:raise ValueError('Checkout Mongo configuration changed: review test adaptation')
    return text.replace('srv := grpc.NewServer(', 'srv := grpc.NewServer(grpc.UnaryInterceptor(df.Unary),',1)


def prepare(root,repo):
    base=root/'runtime/native';base.mkdir(parents=True,exist_ok=True)
    def command(image,arguments,cwd):
        invocation=['singularity','exec','--bind',f'{root}:{root}','--pwd',str(cwd)]
        if image == 'dotnet.sif':
            # Named mutexes need a writable, user-owned runtime directory.
            # Keep it node-local; scratch/NFS and inaccessible container homes
            # can fail during NuGet first-run migration.
            with tempfile.TemporaryDirectory(prefix='df-dotnet-',dir='/tmp') as local_runtime:
                subprocess.run([*invocation,'--bind',f'{local_runtime}:/df-dotnet-runtime',
                                '--env','XDG_RUNTIME_DIR=/df-dotnet-runtime,TMPDIR=/df-dotnet-runtime,TMP=/df-dotnet-runtime,TEMP=/df-dotnet-runtime',
                                str(root/'images'/image),*map(str,arguments)],check=True)
        else:
            subprocess.run([*invocation,str(root/'images'/image),*map(str,arguments)],check=True)
    compiler=root/'runtime/csharp-compiler'
    shutil.copytree(root/'instrumentation/csharp',compiler,dirs_exist_ok=True)
    command('dotnet.sif',['dotnet','build','Instrument.csproj','-c','Release','-o',str(compiler/'out')],compiler)
    cart=base/'cart';cart.mkdir(exist_ok=True)
    source=repo/'src/cart-agent/src'
    for path in source.glob('*.cs'):
        if path.name.lower()!='program.cs':shutil.copy2(path,cart/path.name)
    shutil.copy2(source/'cartservice.csproj',cart/'cartservice.csproj')
    (cart/'protos').mkdir(exist_ok=True)
    shutil.copy2(repo/'src/cart-agent/protos/demo.proto',cart/'protos/demo.proto')
    shutil.copy2(root/'instrumentation/csharp/Program.cs',cart/'program.cs')
    shutil.copy2(root/'instrumentation/csharp/Trace.cs',cart/'DataflowTrace.cs')
    catalogs=[]
    for filename,methods in [('ollamacartagent.cs','AddItemAsync,GetCartAsync,EmptyCartAsync,MergeQuantityWithAgent'),
                             ('cartserviceimpl.cs','AddItem,GetCart,EmptyCart')]:
        original=source/filename;copied=cart/filename;transformed=cart/(filename+'.instrumented')
        # Compiler reads a copy; all outputs remain underneath runtime/native.
        command('dotnet.sif',['dotnet',compiler/'out/Instrument.dll',copied,transformed,
                             'src/cart-agent/src/'+filename,methods],cart)
        catalogs.extend(json.loads(Path(str(transformed)+'.catalog.json').read_text()))
        transformed.replace(copied)
    (cart/'catalog.json').write_text(json.dumps(catalogs,indent=2),encoding='utf-8')
    command('dotnet.sif',['dotnet','publish','cartservice.csproj','-c','Release','-o',cart/'out'],cart)
    checkout=base/'checkout'
    shutil.copytree(repo/'src/checkout-agent',checkout,dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git','.env','bin','obj'))
    trace=checkout/'dataflowtrace';trace.mkdir(exist_ok=True)
    shutil.copy2(root/'instrumentation/go/trace.go',trace/'trace.go')
    main=checkout/'main.go';text=adapt_checkout_startup(main.read_text(encoding='utf-8'))
    main.write_text(text,encoding='utf-8')
    catalogs=[]
    for filename,methods in [('agent/agent.go','PlaceOrder,parseOrderResponse,parseArguments'),
                             ('tools/tools.go','Execute'),('main.go','PlaceOrder')]:
        copied=checkout/filename;output=copied.with_suffix('.instrumented')
        command('go.sif',['go','run',root/'instrumentation/go/instrument.go','--',copied,output,
                         'src/checkout-agent/'+filename,methods],root)
        catalogs.extend(json.loads(Path(str(output)+'.catalog.json').read_text()))
        output.replace(copied)
    (checkout/'catalog.json').write_text(json.dumps(catalogs,indent=2),encoding='utf-8')
    command('go.sif',['go','build','-o',checkout/'checkout-agent','.'],checkout)
    files=[]
    for folder in [repo/'src/cart-agent/src',repo/'src/checkout-agent']:
        files.extend(path for path in folder.rglob('*') if path.is_file() and path.suffix in {'.go','.cs','.csproj','.proto','.mod','.sum'})
    files.append(repo/'src/cart-agent/protos/demo.proto')
    manifest={path.relative_to(repo).as_posix():sha256(path) for path in files}
    (base/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Native test copies built under '+str(base))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path,required=True)
    args=parser.parse_args();prepare(args.root.resolve(),args.repo.resolve())
