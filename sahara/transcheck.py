"""Deterministic transcript-drift detection.

The Live API's transcription language is a hint, not a constraint: one real Hindi call
came back transcribed as Portuguese. The transcript is the pilot's research artifact,
so drifted turns must be *marked*, never silently kept or discarded. Everything here is
a pure function over the text — no model, no network, unit-testable offline.
"""
from __future__ import annotations

# Unicode block per language's primary subtag. English expects Latin; every Indic
# language also tolerates Latin because elders code-switch into English constantly.
_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "hi": (0x0900, 0x097F),   # Devanagari
    "mr": (0x0900, 0x097F),
    "bn": (0x0980, 0x09FF),
    "pa": (0x0A00, 0x0A7F),   # Gurmukhi
    "gu": (0x0A80, 0x0AFF),
    "or": (0x0B00, 0x0B7F),   # Oriya
    "od": (0x0B00, 0x0B7F),   # Sarvam's code for Odia
    "ta": (0x0B80, 0x0BFF),
    "te": (0x0C00, 0x0C7F),
    "kn": (0x0C80, 0x0CFF),
    "ml": (0x0D00, 0x0D7F),
}

MIN_LETTERS = 10          # never flag tiny turns ("हाँ", "Ok", a name)
MIN_EXPECTED_FRACTION = 0.5


def primary(language: str) -> str:
    """'hi-IN' -> 'hi'."""
    return (language or "").split("-")[0].lower()


def _is_latin(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z") or (0x00C0 <= ord(ch) <= 0x024F)


def check(text: str, language: str) -> str:
    """Return a flag string ('' when the text is consistent with the language).

    Two deterministic rules:
    - script_mismatch: most letters are in neither the expected script nor Latin.
    - latin_diacritics: a majority-Latin turn on a non-English call containing accented
      Latin letters (ã é ç ...). Indian English is ASCII; Portuguese is not — this is
      what catches "eu já tomei remédio" even when the script check passes.
    """
    letters = [c for c in text if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return ""
    sub = primary(language)
    rng = _SCRIPT_RANGES.get(sub)
    n_latin = sum(1 for c in letters if _is_latin(c))
    if rng is None:                                   # English call: Latin is the script
        n_expected = n_latin
    else:
        n_expected = n_latin + sum(1 for c in letters if rng[0] <= ord(c) <= rng[1])
    if n_expected / len(letters) < MIN_EXPECTED_FRACTION:
        return "script_mismatch"
    if sub in _SCRIPT_RANGES and n_latin / len(letters) > 0.5:
        if any(_is_latin(c) and ord(c) > 0x7F for c in letters):
            return "latin_diacritics"
    return ""


def language_mismatch(reported: str | None, language: str) -> str:
    """Compare the server-reported transcription language against the parent's.
    English never mismatches — code-switching is normal. Absent stays unknown, not ok."""
    if not reported:
        return ""
    rep = primary(reported)
    if rep in ("", "und"):
        return ""
    allowed = {primary(language), "en"}
    if primary(language) == "od":
        allowed.add("or")                              # Sarvam's od == BCP-47 or
    return "" if rep in allowed else f"lang_mismatch:{reported}"
