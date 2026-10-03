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
_DIGIT_WORDS = {word: str(index) for index, word in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"))}
_SMALL_WORDS = {word: index for index, word in enumerate(
    (*_DIGIT_WORDS, "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"))}
_TENS_WORDS = {word: value for word, value in zip(
    ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"), range(20, 100, 10), strict=True)}
_WORD_INTEGER = "|".join((*_SMALL_WORDS, *_TENS_WORDS))
_WORD_DIGIT = "|".join(_DIGIT_WORDS)
_SPOKEN_DECIMAL = re.compile(
    rf"\b(?P<whole>(?:{_WORD_INTEGER})(?:[ -](?:{_WORD_DIGIT}))?)\s+point\s+"
    rf"(?P<fraction>(?:{_WORD_DIGIT})(?:\s+(?:{_WORD_DIGIT})){{0,5}})"
    r"(?=\s+(?:inches?|inch|mm|cm|meters?|kg|grams?|g|GB|TB|MB|Hz|kHz|MHz|GHz|mAh|Ah|Wh|watts?|W|hours?|minutes?|seconds?)\b)", re.I,
)
_MODEL_SEPARATOR = r"[\s_\-‐‑‒–—]*"
_MODEL_SUFFIXES = {"pro", "max", "plus", "ultra", "mini", "lite", "se", "xl"}
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
    for word, unit in (("megabytes?", "MB"), ("gigabytes?", "GB"), ("terabytes?", "TB")):
        text = re.sub(r"\b" + word + r"\b", unit, text, flags=re.I)

    def spoken_decimal(match: re.Match[str]) -> str:
        words = re.split(r"[ -]", match["whole"].casefold())
        if len(words) == 2:
            if words[0] not in _TENS_WORDS or words[1] not in _DIGIT_WORDS or words[1] == "zero":
                return match[0]
            whole = _TENS_WORDS[words[0]] + int(_DIGIT_WORDS[words[1]])
        else:
            whole = _SMALL_WORDS.get(words[0], _TENS_WORDS.get(words[0], 0))
        fraction = "".join(_DIGIT_WORDS[word] for word in match["fraction"].casefold().split())
        return f"{whole}.{fraction}"

    text = _SPOKEN_DECIMAL.sub(spoken_decimal, text)
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
    tokens = re.findall(r"[a-z0-9]+(?:[-_‐‑‒–—][a-z0-9]+)*", product.casefold())
    iphone = re.search(r"iphone\s*(\d+)(\s+pro)?(\s+max)?", product, re.I)
    if iphone:
        pattern = r"\b(?:iphone\s*\d+(?:\s+pro)?(?:\s+max)?|\d+\s+pro(?:\s+max)?)\b"
        identity = re.sub(r"\s+", "", iphone[0]).casefold()
    else:
        selected = next(((index, re.sub(r"[^a-z0-9]", "", token)) for index, token in enumerate(tokens)
                         if re.fullmatch(r"[a-z]+\d+[a-z0-9]*", re.sub(r"[^a-z0-9]", "", token))), None)
        if selected is None:
            number_index = next((index for index, token in enumerate(tokens) if token.isdigit() and index > 0), None)
            if number_index is None:
                return ()
            selected = (number_index, tokens[number_index - 1] + tokens[number_index])
        index, code = selected
        parts = re.findall(r"[a-z]+|\d+", code)
        pattern = _MODEL_SEPARATOR.join(
            r"\d+" if part.isdigit() else _MODEL_SEPARATOR.join(re.escape(char) for char in part)
            for part in parts
        )
        pattern = rf"(?<!\w){pattern}[a-z]*(?:\s+(?:{'|'.join(sorted(_MODEL_SUFFIXES))}))*(?!\w)"
        suffixes = []
        for token in tokens[index + 1:]:
            if token not in _MODEL_SUFFIXES:
                break
            suffixes.append(token)
        identity = code + "".join(suffixes)
    mentions = list(re.finditer(pattern, excerpt, re.I))
    def named(mention: re.Match) -> str:
        value = re.sub(r"[\s_\-‐‑‒–—]", "", mention[0]).casefold()
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
    current_owner: bool | None = None
    # Clause boundaries bind both 'model weighs value' and 'value for model'.
    # Mixed ownership inside an unsplit clause is ambiguous, never borrowed.
    clauses = re.split(r"(?<=[.!?;])\s+|[,;](?=\s)|\b(?:and|while|whereas|but|compared\s+to|versus|vs\.?)\s+", excerpt, flags=re.I)
    for clause in clauses:
        owners = {is_owned for _, _, is_owned in product_mentions(clause, product)}
        if owners:
            current_owner = next(iter(owners)) if len(owners) == 1 else None
        if current_owner is True:
            owned.append(clause)
    return " ".join(owned)


def without_product_identity(text: str, product: str) -> str:
    words = re.findall(r"\w+", product)
    if not words:
        return text
    # Canonical identity tolerates typography such as Blackshark / Black Shark,
    # while still requiring the entire named identity rather than exempting its numbers.
    identity = r"[\s_-]*".join(re.escape(char) for char in "".join(words))
    return re.sub(r"(?<!\w)" + identity + r"(?!\w)", " ", text, flags=re.I)
