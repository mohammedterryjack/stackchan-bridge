import json,subprocess,sys,os,time
cfg=json.load(open('/app/mcp_config.json'))['mcpServers'][sys.argv[1]]
env={**os.environ,**cfg.get('env',{})}
p=subprocess.Popen([cfg['command'],*cfg['args']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env,bufsize=1)
def s(o):p.stdin.write(json.dumps(o)+"\n");p.stdin.flush()
def r(i):
  while True:
    m=json.loads(p.stdout.readline())
    if m.get('id')==i:return m
s({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}});r(1)
s({"jsonrpc":"2.0","method":"notifications/initialized"})
for i,(name,args) in enumerate(json.loads(sys.argv[2]),2):
  t0=time.time();s({"jsonrpc":"2.0","id":i,"method":"tools/call","params":{"name":name,"arguments":args}})
  m=r(i);dt=time.time()-t0
  txt="".join(c.get('text','') for c in (m.get('result') or {}).get('content',[])) or json.dumps(m)
  print(f"## {name} {json.dumps(args)}");print(txt[:1500]);print(f"--- {len(txt.encode())}B {dt:.1f}s err={(m.get('result') or {}).get('isError') or 'error' in m}")
p.kill()
