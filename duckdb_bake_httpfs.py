import gzip, pathlib, platform, duckdb
ver = duckdb.__version__
plat = "linux_arm64" if platform.machine() in ("aarch64", "arm64") else "linux_amd64"
d = pathlib.Path(f"/opt/duckdb/extensions/v{ver}/{plat}")
d.mkdir(parents=True, exist_ok=True)
p = d / "httpfs.duckdb_extension"
p.write_bytes(gzip.open("/tmp/httpfs.duckdb_extension.gz", "rb").read())
print("wrote", p, p.stat().st_size, "plat", plat)
c = duckdb.connect(config={"extension_directory": "/opt/duckdb/extensions"})
c.execute("LOAD httpfs")
print("httpfs ok", ver)
