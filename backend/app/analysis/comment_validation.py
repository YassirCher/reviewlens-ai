"""Local, bounded checks on original comments; no network or inference calls."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from app.analysis.grounding import statement_mismatches


@lru_cache(maxsize=1)
def _identifier():
    from langid.langid import LanguageIdentifier, model
    return LanguageIdentifier.from_modelstring(model, norm_probs=True)


def confident_language(text: str) -> str | None:
    """Abstain on short/brand-only inputs; probabilities are not fidelity scores."""
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 10 and not any(ord(char) > 255 for char in letters):
        return None
    language, probability = _identifier().classify(text[:800])
    return str(language) if probability >= .85 else None


def non_latin_language(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return len(letters) >= 4 and sum('LATIN' not in unicodedata.name(char, '') for char in letters) / len(letters) > .5


def translation_mismatches(original: str, translation: str) -> bool:
    """Catch explicit numeric/negation loss; this is not a general fidelity proof."""
    def numbers(text: str) -> set[str]:
        normalized = ''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)
        return set(re.findall(r"\d+(?:[.,]\d+)?", normalized))
    if numbers(original) != numbers(translation):
        return True
    negative = r"\b(?:not|never|no|cannot|can't|doesn't|don't|pas|jamais|sin|nunca|nicht|kein)\b|لا\s|ليس|لم\s|नहीं|नहि|不|没"
    return bool(re.search(negative, original, re.I)) != bool(re.search(negative, translation, re.I))


_STOP = set("a an the this that these those it its is are was were be been being for to of in on with and or but as at by from my our your their i we they me us he she his her reviewer reviewers reports reported users owners comments two le la les un une des du de ce cette ces est sont et ou pour dans avec je mon ma mes il elle son ses avons avoir qui que au aux en".split())
_EVALUATION = set("good great excellent like liked liking love loved nice positive bad poor terrible hate hated negative bon bonne bien excellent excellente aime adoré mauvais mauvaise qualité quality".split())


def content_words(text: str) -> set[str]:
    words = set(re.findall(r"[^\W\d_]+", text.casefold())) - _STOP - _EVALUATION
    return {word[:-1] if len(word) > 4 and word.endswith('s') else word for word in words}


def recurring_support(statement: str, excerpts: tuple[str, ...], product: str) -> bool:
    """Conservative shared-text gate; novel paraphrases remain omitted, not guessed."""
    words = content_words(statement)
    if not words:
        return False
    for excerpt in excerpts:
        if statement_mismatches(statement, excerpt, product) or not words <= content_words(excerpt):
            return False
        # Do not erase explicit negation by matching the same content nouns.
        negation = lambda value: bool(re.search(r"\b(?:not|never|no|cannot|can't|doesn't|don't|pas|jamais|sans)\b", value, re.I))
        if negation(statement) != negation(excerpt):
            return False
    return True
