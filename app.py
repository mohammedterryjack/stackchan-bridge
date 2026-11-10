"""The one FastMCP app behind Stack-chan's own tools. Every tool module does `from app import mcp` and registers on
it; server.py imports them all and serves this app over stdio (one process instead of one per module)."""
from fastmcp import FastMCP

mcp = FastMCP("StackChan")
