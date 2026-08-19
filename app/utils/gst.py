"""
Indian GST helpers: place-of-supply determination and B1 tax-code naming.

The rule (CLAUDE.md section 8): goods moving **within** one state attract CGST + SGST;
goods crossing a state border attract IGST. The "from" state is the **branch** the order
is booked against (`BPL_IDAssignedToInvoice`), not the company's head office — a company
with GST registrations in four states bills from whichever branch ships the goods.

Getting this wrong is not a rounding error. An intra-state order booked as IGST puts the
whole tax amount in the wrong ledger, files under the wrong return, and the retailer's
input-credit claim fails.

State resolution, in order of confidence:

1. **GSTIN prefix** — the first two digits are the state code, assigned by the GST
   council. Unambiguous, and present on almost every document we receive.
2. **B1 state code** — the two-letter code B1 itself uses (``MH``, ``DL``).
3. **State name** — free text from a partner's PO ("Maharashtra"). Matched
   case-insensitively against B1's own list.

The code tables below are B1's, read from ``GET /b1s/v2/States?$filter=Country eq 'IN'``
on the live company. Three differ from the more common ISO-style abbreviations —
``BH`` for Bihar (not BR), ``OD`` for Odisha (not OR), ``UA`` for Uttarakhand (not UK).
Following B1 is what matters: these values are compared against data B1 gave us.
"""
from __future__ import annotations

import re
from decimal import Decimal

# B1 two-letter code → canonical state name (source: GET /States, Country eq 'IN').
B1_STATE_NAMES: dict[str, str] = {
    "CH": "Chandigarh", "HR": "Haryana", "HP": "Himachal Pradesh",
    "JK": "Jammu And Kashmir", "RJ": "Rajasthan", "UP": "Uttar Pradesh",
    "UA": "Uttarakhand", "PB": "Punjab", "BH": "Bihar", "SK": "Sikkim",
    "AR": "Arunachal Pradesh", "NL": "Nagaland", "MN": "Manipur", "MZ": "Mizoram",
    "TR": "Tripura", "ML": "Meghalaya", "AS": "Assam", "WB": "West Bengal",
    "JH": "Jharkhand", "OD": "Odisha", "CG": "Chhattisgarh", "MP": "Madhya Pradesh",
    "GJ": "Gujarat", "DD": "Daman And Diu", "DN": "Dadra And Nagar Haveli",
    "MH": "Maharashtra", "KA": "Karnataka", "GA": "Goa", "LD": "Lakshadweep",
    "KL": "Kerala", "TN": "Tamil Nadu", "PY": "Puducherry",
    "AN": "Andaman And Nicobar", "TS": "Telangana", "AP": "Andhra Pradesh",
    "LA": "Ladakh", "DL": "Delhi",
}

# GSTIN's first two digits → B1 state code.
_GSTIN_PREFIX_TO_B1: dict[str, str] = {
    "01": "JK", "02": "HP", "03": "PB", "04": "CH", "05": "UA", "06": "HR",
    "07": "DL", "08": "RJ", "09": "UP", "10": "BH", "11": "SK", "12": "AR",
    "13": "NL", "14": "MN", "15": "MZ", "16": "TR", "17": "ML", "18": "AS",
    "19": "WB", "20": "JH", "21": "OD", "22": "CG", "23": "MP", "24": "GJ",
    "26": "DN", "27": "MH", "29": "KA", "30": "GA", "31": "LD", "32": "KL",
    "33": "TN", "34": "PY", "35": "AN", "36": "TS", "37": "AP", "38": "LA",
}

# Spellings partners use that do not match B1's names character-for-character.
_NAME_ALIASES: dict[str, str] = {
    "orissa": "OD", "pondicherry": "PY", "uttaranchal": "UA",
    "delhi": "DL", "new delhi": "DL", "nct of delhi": "DL",
    "jammu & kashmir": "JK", "jammu and kashmir": "JK",
    "andaman & nicobar": "AN", "andaman and nicobar islands": "AN",
    "dadra & nagar haveli": "DN", "daman & diu": "DD",
    "telengana": "TS", "chattisgarh": "CG", "pondicherry (puducherry)": "PY",
}

_NAME_TO_B1 = {v.lower(): k for k, v in B1_STATE_NAMES.items()} | _NAME_ALIASES

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def state_from_gstin(gstin: str | None) -> str | None:
    """B1 state code from a GSTIN's numeric prefix, or None if unusable."""
    if not gstin:
        return None
    digits = gstin.strip()[:2]
    return _GSTIN_PREFIX_TO_B1.get(digits)


def normalize_state(value: str | None) -> str | None:
    """
    Best-effort B1 two-letter state code from a code or a name.

    Returns None rather than guessing — the caller must decide what an unknown state
    means, because silently defaulting would pick a tax treatment on no evidence.
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    upper = v.upper()
    if upper in B1_STATE_NAMES:
        return upper
    return _NAME_TO_B1.get(v.lower())


def resolve_state(*, gstin: str | None = None, state: str | None = None) -> str | None:
    """State code, preferring the GSTIN prefix over free-text state."""
    return state_from_gstin(gstin) or normalize_state(state)


def is_interstate(from_state: str | None, to_state: str | None) -> bool | None:
    """
    True when the movement crosses a state border, False when it does not, and
    **None when it cannot be determined** — an unknown state is not an intra-state
    movement, and treating it as one would silently pick CGST+SGST.
    """
    a, b = normalize_state(from_state), normalize_state(to_state)
    if a is None or b is None:
        return None
    return a != b


def format_rate(rate: float | int | str | Decimal) -> str:
    """
    B1 tax codes carry the *combined* GST rate with no trailing zeros: 5, 12, 18, 2.5.
    """
    value = float(rate)
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def vat_group(rate: float | int | str | Decimal, *, interstate: bool) -> str:
    """
    B1 sales tax code for a combined GST rate.

    Naming is this company's own convention, confirmed against posted orders in
    TESTECPL260422: ``CSGST@5`` for intra-state (the CGST+SGST pair) and ``IGST@5``
    for inter-state. The number is the **combined** rate, so a 5% GST line is
    ``CSGST@5`` carrying 2.5% CGST + 2.5% SGST — not ``CSGST@2.5``.
    """
    return f"{'IGST' if interstate else 'CSGST'}@{format_rate(rate)}"
