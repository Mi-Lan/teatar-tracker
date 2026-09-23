"""Text normalisation so Cyrillic, Latin and ALL-CAPS spellings of a title compare equal.

"Дивље месо", "Divlje meso" and "DIVLjE MESO" all normalise to "divlje meso".
"""

from __future__ import annotations

import re
import unicodedata

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž",
    "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n",
    "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "ć", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š",
}

# Serbian month names (and short forms) → month number, keyed by their first 3 Latin letters.
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "avg": 8, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12,
}


def to_latin(text: str) -> str:
    """Transliterate Serbian Cyrillic to Latin, preserving case of the first letter."""
    out = []
    for ch in text:
        low = ch.lower()
        if low in _CYR_TO_LAT:
            lat = _CYR_TO_LAT[low]
            out.append(lat.capitalize() if ch != low else lat)
        else:
            out.append(ch)
    return "".join(out)


def normalize(text: str) -> str:
    """Lowercase ASCII form used for matching and grouping titles."""
    text = to_latin(text or "").casefold().replace("đ", "dj")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def month_number(name: str) -> int | None:
    """Map 'септембар', 'sep', 'Oktobra', 'окт' … to a month number."""
    key = normalize(name)[:3]
    return _MONTHS.get(key)


def matches(query: str, title: str) -> bool:
    """True when every word of the query appears in the title (order-independent)."""
    q = normalize(query).split()
    t = normalize(title)
    return bool(q) and all(word in t for word in q)
