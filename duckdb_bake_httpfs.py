# Fetch DuckDB httpfs at image build time for the build arch; fail loudly on any error.
import gzip, pathlib, platform, urllib.request, duckdb
ver = duckdb.__version__
m = platform.machine()
plat = {"aarch64": "linux_arm64", "arm64": "linux_arm64", "x86_64": "linux_amd64", "amd64": "linux_amd64"}[m]
url = f"http://extensions.duckdb.org/v{ver}/{plat}/httpfs.duckdb_extension.gz"
d = pathlib.Path(f"/opt/duckdb/extensions/v{ver}/{plat}")
d.mkdir(parents=True, exist_ok=True)
p = d / "httpfs.duckdb_extension"
with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=120) as r:
    p.write_bytes(gzip.decompress(r.read()))
print("wrote", p, p.stat().st_size, "from", url)
c = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
c.execute("LOAD httpfs")
print("httpfs ok", ver)
