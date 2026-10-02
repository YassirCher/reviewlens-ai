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
    r"kHz|MHz|GHz|Hz|kW|watts?|W|GB|TB|MB|MP|fps|inches?|inch|%|milliseconds?|ms|seconds?|secs?|hours?|hrs?|minutes?|mins?)(?!\w)", re.I,
)
_CAPACITY_WORDS = re.compile(r"\b(milli\s*amp(?:ere)?|amp(?:ere)?)\s*[- ]?hours?\b", re.I)
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
    **{unit: ("duration", Fraction(scale)) for units, scale in (
        (("millisecond", "milliseconds", "ms"), "0.001"), (("second", "seconds", "sec", "secs"), 1),
        (("minute", "minutes", "min", "mins"), 60), (("hour", "hours", "hr", "hrs"), 3600)) for unit in units},
}


def decimal(raw: str) -> Fraction:
    # An unambiguous decimal comma differs from a three-digit thousands group.
    if "," in raw:
        raw = raw.replace(",", "" if len(raw.rsplit(",", 1)[1]) == 3 else ".")
    return Fraction(raw)


def normalize_quantity_words(text: str) -> str:
    text = _CAPACITY_WORDS.sub(lambda m: "mAh" if m[1].casefold().startswith("milli") else "Ah", text)
    text = re.sub(r'এমএম|एमएम', ' mm ', text)
    # Hindi/Bengali unit words normalize only for validation. Stored quotations
    # remain verbatim; no model translation or additional call is required.
    for pattern, unit in ((r"ঘ(?:ন্টা|ণ্টা)(?:র)?|घंट(?:े|ा)", "hours"),
                          (r"মিনিট(?:ের)?|मिनट", "minutes"),
                          (r"সেকেন্ড(?:ের)?|सेकंड", "seconds")):
        text = re.sub(pattern, ' ' + unit + ' ', text)
    return text


def explicit_quantities(text: str) -> tuple[set[tuple[str, Fraction]], str]:
    text = normalize_quantity_words(text)
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


def product_mentions(excerpt: str, product: str) -> tuple[tuple[int, int, bool], ...]:
    """Offsets and ownership of explicit mentions in the requested model family."""
    tokens = re.findall(r"[a-z0-9]+", product.casefold())
    code = next((token for token in tokens if re.fullmatch(r"[a-z]+\d+[a-z]*", token)), None)
    if not code:
        # Conservative iPhone family matching includes all variant suffixes.
        match = re.search(r"iphone\s*(\d+)(\s+pro)?(\s+max)?", product, re.I)
        if not match:
            return ()
        pattern = r"\b(?:iphone\s*\d+(?:\s+pro)?(?:\s+max)?|\d+\s+pro(?:\s+max)?)\b"
        identity = re.sub(r"\s+", "", match[0]).casefold()
    else:
        family = re.match(r"[a-z]+", code)[0]
        pattern = rf"\b{family}\d+[a-z]*\b"
        identity = code
    mentions = list(re.finditer(pattern, excerpt, re.I))
    def named(mention: re.Match) -> str:
        value = re.sub(r"\s+", "", mention[0]).casefold()
        if identity.startswith('iphone') and not value.startswith('iphone'):
            value = 'iphone' + value
        return value[:-1] if value == identity + "s" else value
    return tuple((m.start(), m.end(), named(m) == identity) for m in mentions)


def product_passage(excerpt: str, product: str) -> str:
    """Bind numeric support to explicit sibling mentions when present."""
    mentions = product_mentions(excerpt, product)
    if not mentions:
        return excerpt
    if all(owned for _, _, owned in mentions):
        return excerpt
    owned = []
    for index, (start, _, is_owned) in enumerate(mentions):
        if is_owned:
            end = mentions[index + 1][0] if index + 1 < len(mentions) else len(excerpt)
            owned.append(excerpt[0 if index == 0 else start:end])
    return " ".join(owned)


def without_product_identity(text: str, product: str) -> str:
    words = re.findall(r"\w+", product)
    if not words:
        return text
    # Canonical identity tolerates typography such as Blackshark / Black Shark,
    # while still requiring the entire named identity rather than exempting its numbers.
    identity = r"[\s_-]*".join(re.escape(char) for char in "".join(words))
    return re.sub(r"(?<!\w)" + identity + r"(?!\w)", " ", text, flags=re.I)
