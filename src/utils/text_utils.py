"""Text utility functions for name normalization and string processing.

Centralized text processing utilities used across extraction modules.
Supports all European languages including Germanic, Romance, Slavic,
Baltic, Celtic, Uralic, and Hellenic language families.
"""

import re
import unicodedata
from functools import lru_cache

# European transliteration map for characters that unicodedata doesn't handle well
_EUROPEAN_TRANSLITERATIONS = {
    # Germanic: German, Dutch, Scandinavian, Icelandic
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
    "ij": "ij",
    "å": "aa",
    "Å": "Aa",
    "ø": "oe",
    "Ø": "Oe",
    "æ": "ae",
    "Æ": "Ae",
    "ð": "d",
    "Ð": "D",
    "þ": "th",
    "Þ": "Th",
    # Romance: French, Spanish, Portuguese, Italian, Romanian
    "œ": "oe",
    "Œ": "Oe",
    "ç": "c",
    "Ç": "C",
    "ñ": "n",
    "Ñ": "N",
    "ã": "a",
    "Ã": "A",
    "õ": "o",
    "Õ": "O",
    "ă": "a",
    "Ă": "A",
    "ș": "s",
    "Ș": "S",
    "ț": "t",
    "Ț": "T",
    "î": "i",
    "Î": "I",
    # Slavic: Polish, Czech, Slovak, Croatian, Serbian, Slovenian, Bulgarian
    "ł": "l",
    "Ł": "L",
    "ź": "z",
    "Ź": "Z",
    "ż": "z",
    "Ż": "Z",
    "ą": "a",
    "Ą": "A",
    "ę": "e",
    "Ę": "E",
    "ś": "s",
    "Ś": "S",
    "ć": "c",
    "Ć": "C",
    "ń": "n",
    "Ń": "N",
    "ř": "r",
    "Ř": "R",
    "š": "s",
    "Š": "S",
    "č": "c",
    "Č": "C",
    "ž": "z",
    "Ž": "Z",
    "ě": "e",
    "Ě": "E",
    "ů": "u",
    "Ů": "U",
    "ď": "d",
    "Ď": "D",
    "ť": "t",
    "Ť": "T",
    "ň": "n",
    "Ň": "N",
    "đ": "d",
    "Đ": "D",
    # Baltic: Lithuanian, Latvian
    "ą": "a",
    "ę": "e",
    "ė": "e",
    "Ė": "E",
    "į": "i",
    "Į": "I",
    "ų": "u",
    "Ų": "U",
    "ū": "u",
    "Ū": "U",
    "ķ": "k",
    "Ķ": "K",
    "ļ": "l",
    "Ļ": "L",
    "ņ": "n",
    "Ņ": "N",
    "ģ": "g",
    "Ģ": "G",
    # Celtic: Irish, Welsh
    "ẁ": "w",
    "ẃ": "w",
    "ŵ": "w",
    "ỳ": "y",
    "ý": "y",
    "ŷ": "y",
    "Ý": "Y",
    # Uralic: Hungarian, Finnish, Estonian
    "ő": "o",
    "Ő": "O",
    "ű": "u",
    "Ű": "U",
    # Greek transliteration
    "α": "a",
    "β": "v",
    "γ": "g",
    "δ": "d",
    "ε": "e",
    "ζ": "z",
    "η": "i",
    "θ": "th",
    "ι": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ξ": "x",
    "ο": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s",
    "ς": "s",
    "τ": "t",
    "υ": "y",
    "φ": "f",
    "χ": "ch",
    "ψ": "ps",
    "ω": "o",
    "Α": "A",
    "Β": "V",
    "Γ": "G",
    "Δ": "D",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "I",
    "Θ": "Th",
    "Ι": "I",
    "Κ": "K",
    "Λ": "L",
    "Μ": "M",
    "Ν": "N",
    "Ξ": "X",
    "Ο": "O",
    "Π": "P",
    "Ρ": "R",
    "Σ": "S",
    "Τ": "T",
    "Υ": "Y",
    "Φ": "F",
    "Χ": "Ch",
    "Ψ": "Ps",
    "Ω": "O",
}

# Build regex pattern for transliteration (longest match first)
_TRANSLIT_PATTERN = re.compile(
    "|".join(
        re.escape(k) for k in sorted(_EUROPEAN_TRANSLITERATIONS, key=len, reverse=True)
    )
)


@lru_cache(maxsize=2000)
def transliterate(text: str) -> str:
    """Transliterate European characters to ASCII equivalents.

    Uses language-aware mappings (e.g., German ö→oe, not just o).
    Falls back to Unicode NFKD decomposition for unmapped characters.

    Args:
        text: Text with European characters

    Returns:
        ASCII transliteration
    """
    # Apply explicit transliterations first
    result = _TRANSLIT_PATTERN.sub(
        lambda m: _EUROPEAN_TRANSLITERATIONS[m.group()], text
    )
    # Fall back to Unicode decomposition for remaining diacritics
    result = unicodedata.normalize("NFKD", result)
    return result.encode("ascii", "ignore").decode("ascii")


@lru_cache(maxsize=1000)
def normalize_name(name: str) -> str:
    """Normalize person or group name for index matching.

    Collapses common variations that should map to the same entity:
    - Case folding
    - Punctuation removal (commas, periods)
    - Unicode → ASCII (handles accents, umlauts)
    - Whitespace normalization
    """
    import re
    import unicodedata

    name = name.strip().lower()
    # ASCII-fold (Döllmann → dollmann, Côte → cote)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    # Remove punctuation that causes false splits
    name = name.replace(",", "").replace(".", "").replace("'", "")
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


@lru_cache(maxsize=1000)
def normalize_name_ascii(name: str) -> str:
    """Normalize name to lowercase ASCII for cross-language matching.

    Combines transliteration with lowercasing for matching names across
    different European language spellings (e.g., Dönitz ↔ Doenitz ↔ Donitz).

    Args:
        name: Person or group name in any European language

    Returns:
        Normalized ASCII lowercase name
    """
    return transliterate(name).strip().lower()


@lru_cache(maxsize=500)
def normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces to single space.

    Args:
        text: Text to normalize

    Returns:
        Text with normalized whitespace
    """
    return re.sub(r"\s+", " ", text.strip())


@lru_cache(maxsize=500)
def remove_special_chars(text: str, keep_spaces: bool = True) -> str:
    """Remove special characters from text, keeping only alphanumeric.

    Args:
        text: Text to clean
        keep_spaces: Whether to keep spaces (default: True)

    Returns:
        Cleaned text
    """
    if keep_spaces:
        return re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return re.sub(r"[^a-zA-Z0-9]", "", text)


@lru_cache(maxsize=500)
def to_filename_safe(text: str, max_length: int = 50) -> str:
    """Convert text to filesystem-safe filename component.

    Transliterates European characters, removes special characters,
    and replaces spaces with underscores.

    Args:
        text: Text to convert
        max_length: Maximum length of result (default: 50)

    Returns:
        Filesystem-safe string
    """
    safe = transliterate(text)
    safe = re.sub(r"[^a-zA-Z0-9\s]", "", safe)
    safe = re.sub(r"\s+", "_", safe.strip())
    return safe[:max_length]


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to maximum length with optional suffix.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


@lru_cache(maxsize=200)
def extract_initials(name: str) -> str:
    """Extract initials from a name.

    Args:
        name: Full name

    Returns:
        Initials (uppercase)
    """
    parts = name.split()
    initials = []
    for part in parts:
        if part and part[0].isalpha():
            initials.append(part[0].upper() + ".")
    return "".join(initials)


def similarity_ratio(text1: str, text2: str) -> float:
    """Calculate similarity ratio between two texts.

    Compares both original and ASCII-transliterated versions,
    returning the higher score. This handles cross-language matching
    (e.g., Dönitz vs Donitz).

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity ratio (0.0 to 1.0)
    """
    from difflib import SequenceMatcher

    original = SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    ascii_ratio = SequenceMatcher(
        None, normalize_name_ascii(text1), normalize_name_ascii(text2)
    ).ratio()
    return max(original, ascii_ratio)
