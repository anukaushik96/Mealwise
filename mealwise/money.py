"""Rupee parsing for Swiggy responses.

Swiggy returns money as display strings - "₹234.00", "₹257", "FREE",
"₹1,234.50" - and sometimes as bare numbers. All arithmetic here is in
integer paise so repeated adds cannot drift the way floats do.
"""

import re

RUPEE = "₹"
_CLEAN = re.compile(r"[%s,\s]" % RUPEE)
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")

# Labels Swiggy uses for a zero charge rather than "0".
_FREE_WORDS = {"free", "freebie", "waived", "na", "n/a", "-", ""}


def parse_paise(value):
    """Parse a Swiggy money value into integer paise. Unparseable -> None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return int(round(value * 100))

    text = str(value).strip()
    if text.lower() in _FREE_WORDS:
        return 0

    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    cleaned = _CLEAN.sub("", text).lstrip("-").strip("()")
    if not _NUMERIC.match(cleaned):
        return None
    paise = int(round(float(cleaned) * 100))
    return -paise if negative else paise


def rupees(paise):
    """Format integer paise for display, trimming a pointless .00."""
    if paise is None:
        return "?"
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    if frac:
        return "%s%s%s.%02d" % (sign, RUPEE, "{:,}".format(whole), frac)
    return "%s%s%s" % (sign, RUPEE, "{:,}".format(whole))
