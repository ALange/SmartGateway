"""Output validation: does the model's answer parse as JSON and match a JSON schema?"""
import json
import re
from typing import Optional

import jsonschema

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def check_schema(schema: dict) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)


def validate_json(text: str, schema: Optional[dict]) -> tuple:
    """-> (ok, error message). With no schema, only checks that the text is valid JSON."""
    t = (text or "").strip()
    if not t:
        return False, "the reply is empty"
    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    try:
        value = json.loads(t)
    except ValueError as e:
        return False, f"the reply is not valid JSON ({e})"
    if schema:
        errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
        if errs:
            lines = [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message[:160]}" for e in errs[:5]]
            return False, "the reply does not match the JSON schema: " + "; ".join(lines)
    return True, ""
