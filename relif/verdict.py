"""The "is this deductible?" decision tree: a pure, table-driven function over a few answers.

Informational only. Verdicts are phrased as general CRA guidance with a citation, never as advice.
Lines and CCA classes follow CRA guide T4036 (Rental Income); check them each tax year.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CURRENT, CAPITAL, PARTIAL, NOT_DEDUCTIBLE, UNSURE = "current", "capital", "partial", "not_deductible", "unsure"

T4036 = "CRA T4036 Rental Income"
T4036_URL = "https://www.canada.ca/en/revenue-agency/services/forms-publications/publications/t4036/rental-income.html"
DISCLAIMER = "Not tax advice. Based on general CRA guidance (T4036); confirm with the CRA or a tax professional."

# kind -> (T776 line, category name, citation section)
CURRENT_KINDS = {
    "advertising": ("8521", "Advertising", "Expenses you can deduct"),
    "insurance": ("8690", "Insurance", "Expenses you can deduct"),
    "interest": ("8710", "Interest and bank charges", "Interest"),
    "office": ("8810", "Office expenses", "Expenses you can deduct"),
    "professional": ("8860", "Professional fees", "Legal, accounting and other professional fees"),
    "management": ("8871", "Management and administration fees", "Expenses you can deduct"),
    "repair": ("8960", "Repairs and maintenance", "Current or capital expenses"),
    "wages": ("9060", "Salaries, wages, and benefits", "Salaries, wages, and benefits"),
    "property_tax": ("9180", "Property taxes", "Expenses you can deduct"),
    "travel": ("9200", "Travel", "Travel expenses"),
    "utilities": ("9220", "Utilities", "Utilities"),
    "vehicle": ("9281", "Motor vehicle expenses", "Motor vehicle expenses"),
}
# kind -> (CCA class, description)
CAPITAL_KINDS = {
    "appliance": ("8", "Class 8 (20%): appliances, furniture and tools of $500 or more"),
    "improvement": ("1", "Class 1 (4%): buildings acquired after 1987 and additions to them"),
    "fence": ("6", "Class 6 (10%): fences"),
}
NEVER_KINDS = {
    "own_labour": "the value of your own labour is not deductible",
    "penalty": "penalties and fines are not deductible",
    "land_transfer_tax": "land transfer tax is added to the cost of the property, not deducted",
}
UNSURE_KINDS = {
    "roof": "a roof can be a repair or a capital replacement; the facts decide",
    "landscaping": "landscaping treatment depends on the facts",
    "other": "this kind of purchase is not in RELIF's rules yet",
}
KINDS = sorted({*CURRENT_KINDS, *CAPITAL_KINDS, *NEVER_KINDS, *UNSURE_KINDS, "loan_payment"})

USES = ("rental", "shared", "personal")        # Q1: only a rented unit / shared with your own unit / your own unit
TRI = ("yes", "no", "unsure")
WORK = ("restores", "improves", "separate_asset", "unsure")   # Q5: current vs capital tests
LOAN_PARTS = ("interest", "principal")


@dataclass
class Answers:
    kind: Optional[str] = None           # Q3 what was bought
    use: str = "rental"                  # Q1
    rental_pct: int = 100                # Q1 rented share of a shared property (area or rooms)
    available: str = "yes"               # Q2 available for rent in this period
    loan_part: Optional[str] = None      # Q4
    work: Optional[str] = None           # Q5 for repairs
    personal_pct: int = 0                # Q6 personal share of a mixed purchase
    short_term: bool = False             # Q7 short-term rental (< 90 days)
    str_compliant: Optional[str] = None  # Q7
    profit: str = "yes"                  # Q8 reasonable expectation of profit

    def validate(self) -> None:
        def one_of(name, value, allowed, optional=False):
            if value is None and optional:
                return
            if value not in allowed:
                raise ValueError(f"{name} must be one of {', '.join(allowed)} (got {value!r})")
        one_of("kind", self.kind, KINDS, optional=True)
        one_of("use", self.use, USES)
        one_of("available", self.available, TRI)
        one_of("loan_part", self.loan_part, LOAN_PARTS, optional=True)
        one_of("work", self.work, WORK, optional=True)
        one_of("str_compliant", self.str_compliant, TRI, optional=True)
        one_of("profit", self.profit, TRI)
        for name in ("rental_pct", "personal_pct"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be a whole number from 0 to 100 (got {value!r})")


@dataclass
class Verdict:
    outcome: str
    label: str
    reason: str
    citation: str
    t776_line: Optional[str] = None
    category: Optional[str] = None
    cca_class: Optional[str] = None
    deductible_pct: int = 0
    assumptions: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    @property
    def is_capital(self) -> bool:
        return self.outcome == CAPITAL


def verdict_lines(v: Verdict) -> list:
    """The verdict as text lines; `relif ask` and the demo page both print exactly these."""
    return [v.label, f"  why: {v.reason}", *(f"  {note}" for note in v.assumptions),
            f"  source: {v.citation}", f"  {v.disclaimer}"]


def _cite(section: str) -> str:
    return f'{T4036}, "{section}" ({T4036_URL})'


def _no(reason: str, section: str, assumptions) -> Verdict:
    return Verdict(NOT_DEDUCTIBLE, f"Not generally deductible: {reason}", reason, _cite(section),
                   assumptions=assumptions)


def _unsure(reason: str, section: str, assumptions) -> Verdict:
    return Verdict(UNSURE, f"Unsure: ask an accountant ({reason})", reason, _cite(section), assumptions=assumptions)


def decide(a: Answers) -> Verdict:
    a.validate()
    notes = []
    if a.use == "personal":
        return _no("it is for your own (non-rental) unit", "Expenses you can deduct", notes)
    if a.available != "yes":
        return _unsure("the property may not be available for rent in this period", "Expenses you can deduct", notes)
    if a.profit == "no":
        return _no("there is no reasonable expectation of profit (e.g. below-market rent to relatives)",
                   "Renting below fair market value", notes)
    if a.profit == "unsure":
        return _unsure("reasonable expectation of profit is unclear", "Renting below fair market value", notes)
    if a.short_term:
        notes.append("short-term rental")
        if a.str_compliant == "no":
            return _no("the short-term rental is not compliant with local licensing rules",
                       "Non-compliant short-term rentals", notes)
        if a.str_compliant != "yes":
            return _unsure("short-term rental licensing compliance is unknown", "Non-compliant short-term rentals",
                           notes)

    pct = 100
    if a.use == "shared":
        pct = a.rental_pct
        notes.append(f"assuming the rented share of the property is {a.rental_pct}%")
    if a.personal_pct:
        pct = (pct * (100 - a.personal_pct) + 50) // 100  # whole percent, half-up
        notes.append(f"assuming {a.personal_pct}% of the purchase is personal")

    if pct == 0:
        return _no("none of it is for the rental", "Expenses you can deduct", notes)

    kind = a.kind
    if kind is None:
        return _unsure("the kind of purchase is not given", "Expenses you can deduct", notes)
    if kind in NEVER_KINDS:
        return _no(NEVER_KINDS[kind], "Expenses you cannot deduct", notes)
    if kind in UNSURE_KINDS:
        return _unsure(UNSURE_KINDS[kind], "Current or capital expenses", notes)
    if kind == "loan_payment":
        if a.loan_part == "principal":
            return _no("repaying the principal of a loan is not deductible", "Interest", notes)
        if a.loan_part != "interest":
            return _unsure("split the payment into interest and principal first", "Interest", notes)
        kind = "interest"
    if kind == "repair":
        if a.work in (None, "unsure"):
            return _unsure("it is unclear whether the work restores or improves the property",
                           "Current or capital expenses", notes)
        if a.work != "restores":
            kind = "improvement" if a.work == "improves" else "appliance"

    if kind in CAPITAL_KINDS:
        cls, desc = CAPITAL_KINDS[kind]
        share = f"; only the rental share ({pct}%) goes in the class" if pct < 100 else ""
        return Verdict(CAPITAL, f"Likely capital: CCA {desc} may apply",
                       f"It gives a lasting benefit or is a separate asset, so it is claimed through capital cost "
                       f"allowance, not as a current expense; the half-year rule applies in the year you buy it and "
                       f"CCA cannot create or increase a rental loss{share}.",
                       _cite("Current or capital expenses"), cca_class=cls, deductible_pct=pct, assumptions=notes)

    line, name, section = CURRENT_KINDS[kind]
    category = f"{name} ({line})"
    reason = f"A current expense that does not give a lasting benefit: T776 line {line} ({name})."
    if kind == "vehicle":
        reason += " Keep a mileage log for the rental trips."
    if pct < 100:
        reason += f" Claim {pct}% of it; the personal portion goes on line 9949."
        return Verdict(PARTIAL, f"Partly deductible: {pct}% under {category}", reason, _cite(section),
                       t776_line=line, category=category, deductible_pct=pct, assumptions=notes)
    return Verdict(CURRENT, f"Generally deductible: {category}", reason, _cite(section), t776_line=line,
                   category=category, deductible_pct=100, assumptions=notes)
