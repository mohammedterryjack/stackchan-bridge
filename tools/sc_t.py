import json,subprocess,sys,os,time
os.environ['BOOKMARKS_FILE']='/data/bookmarks/smoke-test.json'  # tests never touch the real bookmarks.json
os.environ['MEMORY_BANK']='stackchan-smoke'  # tests never touch Mo's real memory bank
os.environ['STACKCHAN_WARM']='memory' if sys.argv[2:3] and sys.argv[2].startswith('learn_') else 'none'  # one call per process: skip startup dataset checks; learn_* still gets the Hindsight check and bank creation
HEAD='';EXPECT=None
if sys.argv[1]=='hindsight':  # test helpers for the smoke bank only: reset (delete the bank) / wait (Hindsight up + nothing pending) / wait-reflect
  import httpx;B=os.environ['HINDSIGHT_URL'].rstrip('/');A=B+'/v1/default/banks/'+os.environ['MEMORY_BANK'];t0=time.time();ok=False
  if sys.argv[2]=='reset':
    r=httpx.delete(A,timeout=30);ok=r.status_code in (200,404)
    F=f"/data/bookmarks/reflections-{os.environ['MEMORY_BANK']}.json"
    if os.path.exists(F):os.remove(F)
    print(f"deleted bank {os.environ['MEMORY_BANK']} (memory_server recreates it): HTTP {r.status_code}")
  elif sys.argv[2]=='wait':
    while time.time()-t0<float(json.loads(sys.argv[3] or '{}').get('max_s',300)):
      try:
        if httpx.get(B+'/health',timeout=3).status_code==200:
          n=lambda st:httpx.get(A+'/operations',params={'status':st,'limit':1},timeout=5).json().get('total') or 0
          if not n('pending')+n('processing'):ok=not n('failed');print(f"Hindsight up; pending 0, failed {n('failed')}");break
      except httpx.HTTPError:pass
      time.sleep(2)
    else:print('timed out waiting for Hindsight')
  elif sys.argv[2]=='wait-reflect':  # until the newest reflection job of the smoke bank has finished
    F=f"/data/bookmarks/reflections-{os.environ['MEMORY_BANK']}.json"
    while time.time()-t0<float(json.loads(sys.argv[3] or '{}').get('max_s',1800)):
      j=json.load(open(F))['jobs'][-1]
      if j['status'] in ('done','failed'):ok=j['status']=='done';print(f"reflection {j['status']} after {(j['finished']-(j.get('started') or j['created'])):.0f}s");break
      time.sleep(5)
    else:print('timed out waiting for the reflection')
  print(f"--- {time.time()-t0:.1f}s err={not ok}");sys.exit()
if sys.argv[1]=='bookmarks':  # pipe-served tools: same bookmarks module as mcp_pipe; continue runs the saved read call
  sys.path.insert(0,'/app');import bookmarks;t0=time.time();a=json.loads(sys.argv[3] if len(sys.argv)>3 else '{}')
  try:
    if sys.argv[2]=='bookmark_continue':
      _,tool,targs,HEAD=bookmarks.plan_continue(a.get('book'));sys.argv=[sys.argv[0],'stackchan',tool,json.dumps(targs)]
    else:
      txt=bookmarks.handle(sys.argv[2],a);print(txt);print(f"--- {len(txt.encode())}B {time.time()-t0:.1f}s err=False");sys.exit()
  except (ValueError,KeyError,RuntimeError) as e:
    print(f"Error: {e}");print("--- err=True");sys.exit()
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
a=json.loads(sys.argv[3] if len(sys.argv)>3 and sys.argv[3] else '{}');EXPECT=a.pop('_expect',None)
t0=time.time()
s({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":sys.argv[2],"arguments":a}})
m=r(2);dt=time.time()-t0
txt="".join(c.get('text','') for c in (m.get('result') or {}).get('content',[])) or json.dumps(m)
if HEAD:txt=HEAD+"\n"+txt
err=(m.get('result') or {}).get('isError') or 'error' in m
if EXPECT and EXPECT.lower() not in txt.lower():txt+=f" [expected '{EXPECT}' in reply]";err=True
print(txt);print(f"--- {len(txt.encode())}B {dt:.1f}s err={err}")
p.kill()
