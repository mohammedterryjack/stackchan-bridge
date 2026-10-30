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
if sys.argv[2]=='list':
  s({"jsonrpc":"2.0","id":2,"method":"tools/list"});print(json.dumps([(t['name'],t.get('description'),list(t['inputSchema'].get('properties',{}))) for t in r(2)['result']['tools']],indent=0));p.kill();sys.exit()
t0=time.time()
s({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":sys.argv[2],"arguments":json.loads(sys.argv[3] if len(sys.argv)>3 else '{}')}})
m=r(2);dt=time.time()-t0
txt="".join(c.get('text','') for c in (m.get('result') or {}).get('content',[])) or json.dumps(m)
print(txt);print(f"--- {len(txt.encode())}B {dt:.1f}s err={(m.get('result') or {}).get('isError') or 'error' in m}")
p.kill()
