"""Math calculator MCP. Eval restricted to the math module only."""
from __future__ import annotations

import math

from app import mcp

_SAFE = {"__builtins__": {}, "math": math}


@mcp.tool()
def calculator(python_expression: str) -> str:
    """Maths and sums, e.g. what is 15% of 240. python_expression: Python maths such as 0.15*240 or math.sqrt(2)."""
    expr = (python_expression or "").strip()
    if not expr:
        raise ValueError("python_expression must be non-empty")
    # refuse obvious non-math constructs loudly
    banned = ("import", "open(", "exec", "eval", "os.", "sys.", "subprocess", "__", "random")
    low = expr.lower()
    for b in banned:
        if b in low:
            raise ValueError(f"expression rejected: contains {b!r}; math only")
    result = eval(expr, _SAFE, {})  # noqa: S307 — intentionally restricted globals
    if isinstance(result, float):
        if result.is_integer() and abs(result) < 1e15:
            return str(int(result))
        return f"{result:.10g}"
    if isinstance(result, (int, complex)) and not isinstance(result, bool):
        return str(result)
    raise ValueError(f"result is not a number: {type(result).__name__}")
