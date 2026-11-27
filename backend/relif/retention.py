"""CRA record-retention rules as a pure function.

Not tax advice. The rules encode the CRA guidance "Keeping records" (canada.ca, Guide RC4409):
keep records 6 years from the end of the tax year they relate to; 6 years from the filing date
when a return is filed late; until an objection or appeal is resolved; and records about capital
property until 6 years after the end of the tax year in which the property is disposed of.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

YEARS = 6
CITATION = "CRA RC4409 'Keeping Records' / Income Tax Act s.230(4)"


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


@dataclass(frozen=True)
class Retention:
    retain_until: Optional[date]  # None means keep (no end date yet)
    hold: bool
    rule: str
    citation: str = CITATION

    def eligible_for_deletion(self, today: date) -> bool:
        return not self.hold and self.retain_until is not None and today > self.retain_until


def retention(tax_year: int, *, filed_late_on: Optional[date] = None, on_hold: bool = False,
              is_capital: bool = False, disposed_on: Optional[date] = None) -> Retention:
    if on_hold:
        return Retention(None, True, "Under objection or appeal: keep until it is resolved (no date).")
    if is_capital:
        if disposed_on is None:
            return Retention(None, True, "Records about capital property not yet disposed of: keep indefinitely.")
        until = date(disposed_on.year + YEARS, 12, 31)
        return Retention(until, False,
                         f"Records about capital property disposed {disposed_on.isoformat()}: "
                         f"{YEARS} years after the end of that tax year.")
    default = date(tax_year + YEARS, 12, 31)
    if filed_late_on is not None:
        late = _add_years(filed_late_on, YEARS)
        if late > default:
            return Retention(late, False, f"Return filed late on {filed_late_on.isoformat()}: {YEARS} years from filing.")
    return Retention(default, False, f"{YEARS} years from the end of the tax year ({tax_year}).")
