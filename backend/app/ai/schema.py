"""Check what the model returned.

The model is asked for one JSON object. Its answer is only ever used after it has been parsed and checked here, and then
again by the same rules the company applies to a person's work (brief validation, lyric fit, similarity, source citation).
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```[A-Za-z]*\s*\n(.*?)\n```\s*$", re.DOTALL)


class NoJson(ValueError):
    pass


def extract_json(text: str) -> Any:
    """Parse the answer as JSON. Tolerates a code fence or a sentence around the object; nothing else."""
    t = (text or "").strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError as e:
            raise NoJson(f"the answer is not valid JSON ({e.msg} at character {e.pos})") from e
    raise NoJson("the answer contains no JSON object")


def _type_ok(kind: str, v: Any) -> bool:
    if kind == "string":
        return isinstance(v, str)
    if kind == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "boolean":
        return isinstance(v, bool)
    if kind == "array":
        return isinstance(v, list)
    if kind == "object":
        return isinstance(v, dict)
    if kind == "null":
        return v is None
    return False


def validate(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    """Problems with ``value`` against a small JSON-Schema subset: type, required, properties, items, enum, length and range limits."""
    problems: list[str] = []
    kind = schema.get("type")
    if kind and not _type_ok(kind, value):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            problems.append(f"{path}: needs at least {schema['minLength']} characters")
        if len(value) > schema.get("maxLength", 10**9):
            problems.append(f"{path}: is {len(value)} characters, over the {schema['maxLength']} allowed")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", 10**9):
            problems.append(f"{path}: has {len(value)} items, over the {schema['maxItems']} allowed")
        if "items" in schema:
            for i, item in enumerate(value):
                problems += validate(schema["items"], item, f"{path}[{i}]")
    if isinstance(value, dict):
        for name in schema.get("required", ()):
            if name not in value or value[name] is None:
                problems.append(f"{path}.{name}: is required")
        for name, sub in schema.get("properties", {}).items():
            if name in value and value[name] is not None:
                problems += validate(sub, value[name], f"{path}.{name}")
    return problems


def describe(schema: dict[str, Any], indent: int = 0) -> str:
    """The schema as plain lines for the prompt, so the model is told exactly what is expected."""
    pad = "  " * indent
    kind = schema.get("type", "any")
    notes = []
    if "enum" in schema:
        notes.append("one of " + ", ".join(json.dumps(x) for x in schema["enum"]))
    for key, label in (("minLength", "at least {} characters"), ("maxLength", "at most {} characters"), ("minItems", "at least {} items"),
                       ("maxItems", "at most {} items"), ("minimum", "at least {}"), ("maximum", "at most {}")):
        if key in schema:
            notes.append(label.format(schema[key]))
    if schema.get("description"):
        notes.append(schema["description"])
    head = f"{kind}" + (f" ({'; '.join(notes)})" if notes else "")
    if kind == "object":
        req = set(schema.get("required", ()))
        lines = [head]
        for name, sub in schema.get("properties", {}).items():
            lines.append(f"{pad}  - {name}{'' if name in req else ' (optional)'}: {describe(sub, indent + 2)}")
        return "\n".join(lines)
    if kind == "array" and "items" in schema:
        return f"{head} of " + describe(schema["items"], indent + 1)
    return head
