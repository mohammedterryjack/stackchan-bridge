"""Recipes MCP: live fetch from TheMealDB free public API (test key '1'); small in-memory cache, no storage.
Tools: recipe_suggest, recipe_search, recipe_search(ingredient=...), recipe_read, recipe_ingredients. Fails loudly."""

from __future__ import annotations

import random
import re
import time
import urllib.parse

import httpx
from app import mcp

from mcp_tool_log import get_logger, logged, note

log = get_logger("RECIPES")

BASE = "https://www.themealdb.com/api/json/v1/1/"
TIMEOUT = httpx.Timeout(connect=4, read=6, write=4, pool=4)
CACHE_TTL = 600
CACHE_MAX = 200
PART_CHARS = 700
LIST_MAX = 8
U = "Recipes (TheMealDB):"

_cache: dict[str, tuple[float, dict]] = {}


def _api(path: str, **params: str) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL and path != "random.php":
        return hit[1]
    r = httpx.get(url, timeout=TIMEOUT, headers={"User-Agent": "stackchan-recipes/1.0"})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"TheMealDB returned unexpected data for {path}")
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[url] = (time.monotonic(), data)
    return data


def _meals(path: str, **params: str) -> list[dict]:
    m = _api(path, **params).get("meals")
    return m if isinstance(m, list) else []


def _meal(id_or_name: str) -> dict:
    s = str(id_or_name or "").strip()
    if not s:
        raise ValueError("pass a recipe id or name")
    if s.isdigit():
        ms = _meals("lookup.php", i=s)
    else:
        ms = _meals("search.php", s=s)
        exact = [m for m in ms if m["strMeal"].lower() == s.lower()]
        ms = exact or ms
    if not ms:
        raise ValueError(f"no recipe found for {s!r}; try recipe_search or recipe_suggest")
    return ms[0]


def _ingredients(m: dict) -> list[str]:
    out = []
    for i in range(1, 21):
        ing = (m.get(f"strIngredient{i}") or "").strip()
        qty = (m.get(f"strMeasure{i}") or "").strip()
        if ing:
            out.append(f"{qty} {ing}".strip())
    return out


def _brief(m: dict) -> str:
    bits = [x for x in (m.get("strArea"), m.get("strCategory")) if x]
    return f"{m['strMeal']} (id {m['idMeal']}" + (", " + " ".join(bits) if bits else "") + ")"


def _steps(m: dict) -> list[str]:
    text = re.sub(r"\r", "", m.get("strInstructions") or "")
    text = re.sub(r"(?im)^\s*step\s*\d+\s*:?\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise RuntimeError(f"recipe {m['idMeal']} has no method text")
    sents = re.split(r"(?<=[.!?])\s+", text)
    parts, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > PART_CHARS:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    return parts


def _list(title: str, ms: list[dict]) -> str:
    lines = [title] + [f"- {m['strMeal']} (id {m['idMeal']})" for m in ms[:LIST_MAX]]
    if len(ms) > LIST_MAX:
        lines.append(f"…and {len(ms) - LIST_MAX} more.")
    lines.append("Open with recipe_read(recipe=...) or recipe_ingredients(recipe=...).")
    return "\n".join(lines)


def _summary(m: dict) -> str:
    ing = _ingredients(m)
    return (f"Suggestion: {_brief(m)}. {len(ing)} ingredients, e.g. {', '.join(ing[:5])}. "
            f"Method: {len(_steps(m))} part(s) via recipe_read(recipe='{m['idMeal']}'). "
            f"Shopping list: recipe_ingredients(recipe='{m['idMeal']}').")


def _area(cuisine: str) -> list[dict]:
    ms = _meals("filter.php", a=cuisine)
    if ms:
        return ms
    for row in _meals("list.php", a="list"):
        names = {(row.get("strArea") or "").lower(), (row.get("strCountry") or "").lower()}
        if cuisine.lower() in names:
            for alt in (row.get("strCountry"), row.get("strArea")):
                if alt and alt.lower() != cuisine.lower():
                    ms = _meals("filter.php", a=alt)
                    if ms:
                        return ms
    return []


@mcp.tool(name="recipe_suggest", description=f"{U} suggest one recipe, optionally with an ingredient, cuisine (e.g. Indian) or category (e.g. Vegetarian, Dessert); random otherwise.")
@logged(log)
def recipe_suggest(ingredient: str | None = None, cuisine: str | None = None, category: str | None = None) -> str:
    pools = []
    for key, val in (("i", ingredient), ("a", cuisine), ("c", category)):
        if val and str(val).strip():
            ms = _area(str(val).strip()) if key == "a" else _meals("filter.php", **{key: str(val).strip().replace(" ", "_")})
            if not ms:
                raise ValueError(f"no recipes for {key}={val!r}")
            pools.append({m["idMeal"] for m in ms})
    if pools:
        ids = set.intersection(*pools)
        if not ids:
            raise ValueError("no recipe matches all of those filters together; try fewer")
        m = _meal(random.choice(sorted(ids)))
    else:
        ms = _meals("random.php")
        if not ms:
            raise RuntimeError("TheMealDB random returned nothing")
        m = ms[0]
    note(m["idMeal"])
    return _summary(m)


@mcp.tool(name="recipe_search", description=f"{U} find recipes by dish name (e.g. lasagne) or by main ingredient (e.g. salmon).")
@logged(log)
def recipe_search(name: str | None = None, ingredient: str | None = None) -> str:
    if ingredient and str(ingredient).strip():
        if name and str(name).strip():
            raise ValueError("give either a dish name or an ingredient, not both")
        return _by_ingredient(ingredient)
    if not str(name or "").strip():
        raise ValueError("pass a dish name or an ingredient")
    ms = _meals("search.php", s=str(name).strip())
    if not ms:
        return f"No recipes named like {name!r}. Try recipe_search with an ingredient, or recipe_suggest."
    note(*[m["idMeal"] for m in ms[:LIST_MAX]])
    return _list(f"Recipes matching {name!r}: {len(ms)}", ms)


def _by_ingredient(ingredient: str) -> str:
    if not str(ingredient or "").strip():
        raise ValueError("pass an ingredient")
    ms = _meals("filter.php", i=str(ingredient).strip().replace(" ", "_"))
    if not ms:
        return f"No recipes with main ingredient {ingredient!r}. Try a simpler word, e.g. chicken."
    random.shuffle(ms)
    note(*[m["idMeal"] for m in ms[:LIST_MAX]])
    return _list(f"Recipes with {ingredient}: {len(ms)} (random selection)", ms)


@mcp.tool(name="recipe_read", description=f"{U} read a recipe's method in short parts, by id or name.")
@logged(log)
def recipe_read(recipe: str, part: int = 1) -> str:
    m = _meal(recipe)
    parts = _steps(m)
    p = int(part)
    if not 1 <= p <= len(parts):
        raise ValueError(f"part {p} out of range: 1 to {len(parts)}")
    note(m["idMeal"])
    nxt = f"Next: recipe_read(recipe='{m['idMeal']}', part={p + 1})" if p < len(parts) else "End of method."
    return f"{_brief(m)}. Method part {p} of {len(parts)}:\n{parts[p - 1]}\n{nxt}"


@mcp.tool(name="recipe_ingredients", description=f"{U} the shopping list for a recipe, by id or name.")
@logged(log)
def recipe_ingredients(recipe: str) -> str:
    m = _meal(recipe)
    ing = _ingredients(m)
    if not ing:
        raise RuntimeError(f"recipe {m['idMeal']} has no ingredients listed")
    note(m["idMeal"])
    return f"To buy for {m['strMeal']} ({len(ing)} items): " + "; ".join(ing) + "."
