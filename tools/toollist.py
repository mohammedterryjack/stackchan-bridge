"""Measure what Xiaozhi sees: run inside the bridge, `docker exec -i stackchan-bridge python - [--json] < tools/toollist.py`.
Starts each mcp_config server, asks tools/list, applies the pipe's slimming (and its own tools on MORE_HOST), and
prints bytes per server and per tool; --json prints one sorted JSON line per tool instead (for diffing)."""
import json, os, subprocess, sys
sys.path.insert(0, "/app")
os.environ["MEMORY_BANK"] = "stackchan-smoke"
os.environ["STACKCHAN_WARM"] = "none"
import mcp_pipe
cfg = json.load(open("/app/mcp_config.json"))["mcpServers"]
as_json, total, count, out = "--json" in sys.argv, 0, 0, []
for name, c in cfg.items():
    p = subprocess.Popen([c["command"], *c["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env={**os.environ, **c.get("env", {})}, bufsize=1)
    def send(o): p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
    def recv(i):
        while True:
            line = p.stdout.readline()
            if not line:
                raise RuntimeError(f"{name} exited before replying")
            if json.loads(line).get("id") == i:
                return line
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}); recv(1)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}); line = recv(2); p.kill()
    pl = mcp_pipe._slim_payload(line.strip())
    if name == mcp_pipe.MORE_HOST:
        pl = mcp_pipe.add_more_tool(pl)
    tools = json.loads(pl)["result"]["tools"]
    total += len(pl.encode()); count += len(tools)
    if as_json:
        out += [json.dumps(t, ensure_ascii=False, sort_keys=True) for t in tools]
        continue
    print(f"== {name} {len(pl.encode())}B {len(tools)} tools")
    for t in tools:
        print(f"   {len(json.dumps(t, ensure_ascii=False).encode()):5d} {t['name']}")
if as_json:
    print("\n".join(sorted(out)))
else:
    print("TOTAL", count, total)
