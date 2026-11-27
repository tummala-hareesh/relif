"""Shared glue for front ends (CLI `ask`, `serve`): build Answers with store context and log a verdict."""
from __future__ import annotations

from typing import Iterable, Optional

from .store import Store
from .verdict import NOT_DEDUCTIBLE, UNSURE, Answers, Verdict


def answers_for(store: Store, property_id: Optional[int], *, rental_pct: Optional[int] = None,
                **answers) -> Answers:
    """Answers for `decide`; a shared purchase defaults to the property's stored rented %."""
    if property_id is not None:
        prop = store.property(property_id)
        if rental_pct is None:
            rental_pct = prop["rented_pct"]
    return Answers(rental_pct=100 if rental_pct is None else rental_pct, **answers)


def log_expense(store: Store, verdict: Verdict, *, amount_cents: int, date, vendor: str,
                description: str = None, property_id: int = None, receipts: Iterable = ()) -> int:
    """Logs the entry with the verdict's deductible share: 0 if not deductible, unknown (None) if unsure."""
    pct = None if verdict.outcome == UNSURE else 0 if verdict.outcome == NOT_DEDUCTIBLE else verdict.deductible_pct
    return store.add(amount_cents=amount_cents, date=date, vendor=vendor, description=description,
                     property_id=property_id, category=verdict.category, verdict=verdict.label,
                     is_capital=verdict.is_capital, cca_class=verdict.cca_class, deductible_pct=pct,
                     receipts=receipts)
