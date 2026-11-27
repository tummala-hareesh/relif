"""Form answers -> verdict for the frontend page (frontend/app.py). Ships to the browser with `verdict.py`.

Stdlib only; this file also runs inside Pyodide. The page itself is built by `frontend/build.py`.
"""
from __future__ import annotations

from dataclasses import fields

from relif.verdict import Answers, decide, verdict_lines

_INTS = {"rental_pct", "personal_pct"}
_FIELDS = {f.name for f in fields(Answers)}


def answers_from_form(form: dict) -> Answers:
    """Widget values (strings, '' for not given) -> Answers. Raises ValueError on anything unexpected."""
    unknown = set(form) - _FIELDS
    if unknown:
        raise ValueError(f"unknown answer(s): {', '.join(sorted(unknown))}")
    values = {}
    for key, value in form.items():
        if value == "" or value is None:
            continue
        if key in _INTS and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"{key} must be a whole number from 0 to 100 (got {value!r})") from None
        values[key] = value
    return Answers(**values)


def ask(form: dict) -> dict:
    """Same verdict and text as `relif ask` for the same answers; errors come back as an 'error' outcome."""
    try:
        v = decide(answers_from_form(form))
    except (ValueError, TypeError) as exc:
        return {"outcome": "error", "label": str(exc), "lines": [str(exc)]}
    return {"outcome": v.outcome, "label": v.label, "lines": verdict_lines(v)}
