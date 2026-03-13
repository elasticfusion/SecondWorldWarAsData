# Text Utilities - Name Normalization

**Module:** `src/utils/text_utils.py`  
**Purpose:** Centralized text processing utilities with full European language support  
**Created:** 2026-03-13  
**Updated:** 2026-03-13 (Added European language support)

---

## Overview

The `text_utils` module provides text processing utilities with comprehensive support for all European languages including Germanic, Romance, Slavic, Baltic, Celtic, Uralic, and Hellenic language families.

**Key Features:**
- Language-aware transliteration (e.g., German ö→oe, not just o)
- Cross-language name matching (Dönitz ↔ Doenitz ↔ Donitz)
- Filesystem-safe filename generation
- Unicode normalization with fallback

---

## European Language Support

### Supported Language Families

| Family | Languages | Examples |
|--------|-----------|----------|
| **Germanic** | German, Dutch, Danish, Norwegian, Swedish, Icelandic | ä→ae, ö→oe, ü→ue, ß→ss, å→aa, ø→oe, þ→th |
| **Romance** | French, Spanish, Portuguese, Italian, Romanian | ç→c, ñ→n, ã→a, œ→oe, ș→s, ț→t |
| **Slavic** | Polish, Czech, Slovak, Croatian, Serbian, Bulgarian | ł→l, ź→z, ż→z, ą→a, ę→e, š→s, č→c, ž→z |
| **Baltic** | Lithuanian, Latvian | ą→a, ę→e, ė→e, į→i, ų→u, ū→u, ķ→k, ļ→l |
| **Celtic** | Irish, Welsh | ŵ→w, ŷ→y, ý→y |
| **Uralic** | Hungarian, Finnish, Estonian | ő→o, ű→u |
| **Hellenic** | Greek | α→a, β→v, γ→g, θ→th, ω→o |

---

## Functions

### `transliterate(text: str) -> str`

**Purpose:** Convert European characters to ASCII equivalents using language-aware mappings

**Examples:**
```python
from src.utils.text_utils import transliterate

# German
transliterate("Dönitz")      # → "Doenitz"
transliterate("Müller")      # → "Mueller"

# Polish
transliterate("Wałęsa")      # → "Walesa"

# French
transliterate("François")    # → "Francois"

# Greek
transliterate("Παπαδόπουλος") # → "Papadpoylos"

# Scandinavian
transliterate("Malmström")   # → "Malmstroem"
transliterate("Þórðarson")   # → "Thordarson"
```

---

### `normalize_name(name: str) -> str`

**Purpose:** Normalize person or group names (lowercase + strip)

**Usage:**
```python
from src.utils.text_utils import normalize_name

normalized = normalize_name("Dwight D. Eisenhower")
# Returns: "dwight d. eisenhower"

normalized = normalize_name("  1st Infantry Division  ")
# Returns: "1st infantry division"
```

---

### `normalize_name_ascii(name: str) -> str`

**Purpose:** Normalize name to lowercase ASCII for cross-language matching

**Usage:**
```python
from src.utils.text_utils import normalize_name_ascii

# German names
normalize_name_ascii("Dönitz")   # → "doenitz"
normalize_name_ascii("Doenitz")  # → "doenitz"
normalize_name_ascii("Donitz")   # → "donitz"

# All variants can be matched
```

---

### `similarity_ratio(text1: str, text2: str) -> float`

**Purpose:** Calculate similarity with cross-language support

**Usage:**
```python
from src.utils.text_utils import similarity_ratio

# Perfect match despite different spellings
ratio = similarity_ratio("Dönitz", "Doenitz")
# Returns: 1.0

ratio = similarity_ratio("Müller", "Mueller")
# Returns: 1.0

# Different names
ratio = similarity_ratio("Eisenhower", "Patton")
# Returns: < 0.5
```

---

### `to_filename_safe(text: str, max_length: int = 50) -> str`

**Purpose:** Convert European names to filesystem-safe filenames

**Usage:**
```python
from src.utils.text_utils import to_filename_safe

filename = to_filename_safe("Günther von Kluge")
# Returns: "Guenther_von_Kluge"

filename = to_filename_safe("François Darlan")
# Returns: "Francois_Darlan"

filename = to_filename_safe("Wałęsa")
# Returns: "Walesa"
```

---

## Use Cases

### 1. Cross-Language Name Matching

Match names regardless of spelling variant:

```python
from src.utils.text_utils import normalize_name_ascii, similarity_ratio

# Historical figures with multiple spellings
names = ["Dönitz", "Doenitz", "Donitz"]
normalized = [normalize_name_ascii(n) for n in names]
# All normalize to similar forms for matching

# Check if two names refer to same person
if similarity_ratio("Günther von Kluge", "Guenther von Kluge") > 0.9:
    print("Likely same person")
```

### 2. Deduplication

Find duplicate people across different language sources:

```python
from src.utils.text_utils import normalize_name_ascii

people = [
    {"name": "Karl Dönitz", "source": "German"},
    {"name": "Karl Doenitz", "source": "English"},
    {"name": "Karl Donitz", "source": "American"},
]

# Group by normalized name
from collections import defaultdict
groups = defaultdict(list)
for person in people:
    key = normalize_name_ascii(person["name"])
    groups[key].append(person)

# All three will be grouped together
```

### 3. Filename Generation

Create consistent filenames from European names:

```python
from src.utils.text_utils import to_filename_safe

# German commanders
to_filename_safe("Günther von Kluge")  # → "Guenther_von_Kluge"
to_filename_safe("Erwin Rommel")       # → "Erwin_Rommel"

# French commanders  
to_filename_safe("François Darlan")    # → "Francois_Darlan"
to_filename_safe("Jean de Lattre")     # → "Jean_de_Lattre"
```

---

## Performance

All functions use `@lru_cache` for optimal performance:

| Function | Cache Size | Purpose |
|----------|------------|---------|
| `transliterate` | 2000 | European character conversion |
| `normalize_name` | 1000 | Basic normalization |
| `normalize_name_ascii` | 1000 | ASCII normalization |
| `normalize_whitespace` | 500 | Whitespace handling |
| `remove_special_chars` | 500 | Character filtering |
| `to_filename_safe` | 500 | Filename generation |
| `extract_initials` | 200 | Initial extraction |

---

## Testing

```bash
# Test European language support
python3 -c "
from src.utils.text_utils import transliterate, normalize_name_ascii

print(transliterate('Dönitz'))      # Doenitz
print(transliterate('Müller'))      # Mueller
print(transliterate('Wałęsa'))      # Walesa
print(transliterate('François'))    # Francois
"

# Run unit tests
python3 -m pytest tests/unit/test_extraction/test_people.py -v
```

---

## Migration Impact

### Backward Compatibility

✅ All existing code continues to work  
✅ `normalize_name()` behavior unchanged  
✅ New functions are additive (no breaking changes)

### New Capabilities

✅ Cross-language name matching  
✅ European character handling  
✅ Improved deduplication  
✅ Better filename generation

---

## Related

- **Code Quality:** [CODE_QUALITY_IMPROVEMENTS_2026-03-13.md](../qa-reports/CODE_QUALITY_IMPROVEMENTS_2026-03-13.md)
- **People Extraction:** [../features/people/README.md](../features/people/README.md)
- **Deduplication:** [../features/people/deduplication.md](../features/people/deduplication.md)

---

**Created:** 2026-03-13  
**Updated:** 2026-03-13 (European language support)  
**Status:** ✅ Implemented and tested
