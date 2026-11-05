import json,subprocess,sys,os,collections
sys.path.insert(0,'/app');import mcp_pipe as mp
cap=collections.defaultdict(list)
cfgs=json.load(open('/app/mcp_config.json'))['mcpServers']
calls=collections.defaultdict(list)
for L in sys.argv[1].splitlines():
    L=L.strip()
    if not L or L[0]=='#': continue
    s,t,a=L.split('|',2); calls[s].append((t,a or '{}'))
extra=[l.strip().split('|',2) for l in sys.argv[2].splitlines() if l.strip()]
for s,t,a in extra: calls[s].append((t,a))
only=sys.argv[3:] 
res=collections.defaultdict(list)
for srv,cl in calls.items():
    if only and srv not in only: continue
    cfg=cfgs[srv]; env={**os.environ,**cfg.get('env',{})}
    p=subprocess.Popen([cfg['command'],*cfg['args']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env,bufsize=1)
    def snd(o):p.stdin.write(json.dumps(o)+"\n");p.stdin.flush()
    def rcv(i):
        while True:
            m=json.loads(p.stdout.readline())
            if m.get('id')==i:return m
    snd({"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}});rcv(0)
    snd({"jsonrpc":"2.0","method":"notifications/initialized"})
    for i,(t,a) in enumerate(cl,1):
        reps=3 if 'random' in t else 1
        for _ in range(reps):
            snd({"jsonrpc":"2.0","id":i*10+_,"method":"tools/call","params":{"name":t,"arguments":json.loads(a)}})
            m=rcv(i*10+_)
            txt="".join(c.get('text','') for c in (m.get('result') or {}).get('content',[]))
            res[(srv,t)].append(len(txt))
            mp.cap_reply(json.dumps({'id':1,'result':{'content':[{'type':'text','text':txt}]}}),t) if len(txt)>mp.CAP_MAX else mp._PAGES.update(parts=[txt])
            cap[(srv,t)].append(max(len(mp._part_text(i)) for i in range(1,len(mp._PAGES['parts'])+1)) if len(txt)>mp.CAP_MAX else len(txt))
    p.kill()
for (s,t),v in sorted(res.items(),key=lambda x:-max(x[1])):
    c=cap[(s,t)];print(f"{s}|{t}|n={len(v)}|before avg={sum(v)//len(v)} max={max(v)}|after max part={max(c)}")
