"""Exact explicit quantities; dimensions cannot borrow unrelated numeric support."""
from __future__ import annotations

import re
from fractions import Fraction

_APERTURE = re.compile(r"(?<!\w)f\s*/?\s*(\d+(?:[.,]\d+)?)(?![\w.])", re.I)
# Captured Darija driver quotations abbreviate millimeters as "ملي".
# The abbreviation alone is ambiguous; require explicit driver context.
_DRIVER_CONTEXT = re.compile(r"\b(?:driver|ديناميك|درايفر)\b", re.I)
_DRIVER_MM = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s+ملي\b")
_UNIT = re.compile(
    r"(?<![\w.])(?P<value>\d+(?:[.,]\d+)?)\s*-?\s*"
    r"(?P<unit>mAh|Ah|Wh|kWh|kg|mg|grams?|g|lb|oz|mm|cm|meters?|m|"
    r"kHz|MHz|GHz|Hz|kW|watts?|W|GB|TB|MB|MP|fps|inches?|inch|%)(?!\w)", re.I,
)
_UNITS = {
    "mah": ("capacity", Fraction(1)), "ah": ("capacity", Fraction(1000)),
    "wh": ("energy", Fraction(1)), "kwh": ("energy", Fraction(1000)),
    "g": ("mass", Fraction(1)), "gram": ("mass", Fraction(1)), "grams": ("mass", Fraction(1)),
    "kg": ("mass", Fraction(1000)), "mg": ("mass", Fraction(1, 1000)),
    "lb": ("mass", Fraction("453.59237")), "oz": ("mass", Fraction("28.349523125")),
    "mm": ("length", Fraction(1)), "cm": ("length", Fraction(10)), "m": ("length", Fraction(1000)),
    "meter": ("length", Fraction(1000)), "meters": ("length", Fraction(1000)),
    "inch": ("length", Fraction("25.4")), "inches": ("length", Fraction("25.4")),
    "hz": ("frequency", Fraction(1)), "khz": ("frequency", Fraction(1000)),
    "mhz": ("frequency", Fraction(1000000)), "ghz": ("frequency", Fraction(1000000000)),
    "w": ("power", Fraction(1)), "watt": ("power", Fraction(1)), "watts": ("power", Fraction(1)),
    "kw": ("power", Fraction(1000)), "mb": ("storage", Fraction(1)),
    "gb": ("storage", Fraction(1000)), "tb": ("storage", Fraction(1000000)),
    "mp": ("resolution", Fraction(1)), "fps": ("frame_rate", Fraction(1)), "%": ("percentage", Fraction(1)),
}


def decimal(raw: str) -> Fraction:
    # An unambiguous decimal comma differs from a three-digit thousands group.
    if "," in raw:
        raw = raw.replace(",", "" if len(raw.rsplit(",", 1)[1]) == 3 else ".")
    return Fraction(raw)


def explicit_quantities(text: str) -> tuple[set[tuple[str, Fraction]], str]:
    values: set[tuple[str, Fraction]] = set()
    if _DRIVER_CONTEXT.search(text):
        def driver_mm(match: re.Match) -> str:
            values.add(("length", decimal(match[1])))
            return " "
        text = _DRIVER_MM.sub(driver_mm, text)

    def aperture(match: re.Match) -> str:
        values.add(("aperture", decimal(match[1])))
        return " "

    def measurement(match: re.Match) -> str:
        dimension, scale = _UNITS[match["unit"].casefold()]
        values.add((dimension, decimal(match["value"]) * scale))
        return " "

    return values, _UNIT.sub(measurement, _APERTURE.sub(aperture, text))


def without_product_identity(text: str, product: str) -> str:
    words = re.findall(r"\w+", product)
    if not words:
        return text
    return re.sub(r"(?<!\w)" + r"[\s_-]+".join(map(re.escape, words)) + r"(?!\w)", " ", text, flags=re.I)
