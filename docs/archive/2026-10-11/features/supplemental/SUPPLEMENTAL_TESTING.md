# Testing Supplemental Material Extraction

## Quick Test

### Automated Test (All Phases)

```bash
python3 tests/test_supplemental_complete.py
```

**What it tests:**
- Phase 1: Extracts citations from first event file
- Phase 2: Searches for online resources
- Phase 3: Extracts ISBN, determines copyright
- Validates JSON structure
- Reports results

**Expected output:**
```
Testing Supplemental Material Extraction (All Phases)
============================================================

=== Phase 1: Core Extraction ===
Testing with: output/Breakout_and_Pursuit/chapter1-event.json
✅ Extracted 5 material(s)
✅ Structure valid
   Type: bibliography
   Citation: The Rise and Fall of the Third Reich

=== Phase 2: Search Integration ===
✅ Enriched 3 material(s)
   Found via: archive_org
   URL: https://archive.org/details/...

=== Phase 3: Advanced Features ===
✅ Applied advanced features to 2 material(s)
   Copyright: copyright
   Basis: Under copyright until 2063

============================================================
✅ All phases tested successfully

Test output: output/supplemental_test/chapter1-supplemental.json
```

## Manual Testing

### Phase 1 Only

```bash
# 1. Enable Phase 1
# Edit config.yaml:
supplemental_material:
  enabled: true
  enrich_with_searches: false

# 2. Run pipeline
python3 phase2_extract.py

# 3. Check output
ls -lh output/supplemental/
cat output/supplemental/Breakout_and_Pursuit/chapter1-supplemental.json | jq '.materials | length'
```

**Verify:**
- File created in `output/supplemental/{book}/`
- Materials array populated
- Each material has: MaterialID, EventID, reference_type, citation
- Citation has: author, title, type

### Phase 2 (Search)

```bash
# 1. Enable Phase 2
# Edit config.yaml:
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true

# 2. Run pipeline
python3 phase2_extract.py

# 3. Check search results
cat output/supplemental/Breakout_and_Pursuit/chapter1-supplemental.json | jq '.materials[0].search_metadata'
```

**Verify:**
- `search_metadata` object added
- `found_via` field populated (or null if not found)
- `resource_urls` array populated (if found)
- `availability` changed to "online" (if found)

### Phase 3 (Advanced)

```bash
# 1. Enable Phase 3
# Edit config.yaml:
supplemental_material:
  enabled: true
  enrich_with_searches: true
  extract_isbn: true
  determine_copyright: true

# 2. Run pipeline
python3 phase2_extract.py

# 3. Check copyright status
cat output/supplemental/Breakout_and_Pursuit/chapter1-supplemental.json | jq '.materials[0].copyright_status'
```

**Verify:**
- `copyright_status` object added
- `status` field: "public_domain" or "copyright"
- `determination_basis` explains reasoning
- `author_death_date` populated (or "UNKNOWN")
- Books have `isbn` in citation (if post-1966)

## Integration Testing

### Full Pipeline Test

```bash
# 1. Enable everything
# Edit config.yaml:
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true

# 2. Clean test output
rm -rf output/supplemental_test/

# 3. Run test
python3 tests/test_supplemental_complete.py

# 4. Inspect results
cat output/supplemental_test/*.json | jq '.materials[0]' | head -50
```

### Production Test (One Chapter)

```bash
# 1. Enable all features in config.yaml

# 2. Run on single chapter
python3 phase2_extract.py

# 3. Check logs
grep "supplemental" logs/phase2_extract.log

# 4. Validate output
python3 -c "
import json
from pathlib import Path

files = list(Path('output/supplemental').rglob('*.json'))
print(f'Found {len(files)} supplemental files')

for f in files[:3]:
    with open(f) as fp:
        data = json.load(fp)
    materials = data.get('materials', [])
    print(f'{f.name}: {len(materials)} materials')
"
```

## Validation Tests

### JSON Schema Validation

```bash
# Validate against schema
python3 -c "
import json
from pathlib import Path
from jsonschema import validate
from src.json_schemas import SUPPLEMENTAL_SCHEMA

file = Path('output/supplemental_test/chapter1-supplemental.json')
with open(file) as f:
    data = json.load(f)

try:
    validate(instance=data, schema=SUPPLEMENTAL_SCHEMA)
    print('✅ Schema validation passed')
except Exception as e:
    print(f'❌ Schema validation failed: {e}')
"
```

### ULID Validation

```bash
# Check ULIDs are valid
python3 -c "
import json
from pathlib import Path
import ulid

file = Path('output/supplemental_test/chapter1-supplemental.json')
with open(file) as f:
    data = json.load(f)

for m in data.get('materials', []):
    try:
        ulid.parse(m['MaterialID'])
        ulid.parse(m['EventID'])
        print(f'✅ Valid ULIDs: {m[\"MaterialID\"][:8]}...')
    except Exception as e:
        print(f'❌ Invalid ULID: {e}')
        break
"
```

### Copyright Logic Test

```bash
# Test copyright determination
python3 -c "
from src.extraction.supplemental_advanced import determine_copyright_status

# Test cases
tests = [
    {'publication_date': '1920-01-01', 'expected': 'public_domain'},
    {'publication_date': '1950-01-01', 'expected': 'public_domain'},  # 1950+95=2045
    {'publication_date': '1980-01-01', 'expected': 'copyright'},
]

for test in tests:
    citation = {'publication_date': test['publication_date'], 'publisher': ''}
    result = determine_copyright_status(citation, None, 'USA')
    status = result['status']
    expected = test['expected']
    symbol = '✅' if status == expected else '❌'
    print(f'{symbol} {test[\"publication_date\"]}: {status} (expected {expected})')
"
```

## Performance Testing

### Timing Test

```bash
# Time each phase
time python3 -c "
from pathlib import Path
from src.extraction.supplemental import extract_supplemental
from src.grok_client import GrokClient

event_file = list(Path('output').rglob('*-event.json'))[0]
grok = GrokClient(Path('cache'))

extract_supplemental(event_file, grok, Path('output/supplemental_test'))
print('Phase 1 complete')
"
```

### Cache Test

```bash
# Verify caching works
python3 tests/test_supplemental_complete.py  # First run
python3 tests/test_supplemental_complete.py  # Second run (should be faster)
```

## Error Testing

### Missing Event File

```bash
python3 -c "
from pathlib import Path
from src.extraction.supplemental import extract_supplemental
from src.grok_client import GrokClient

result = extract_supplemental(
    Path('nonexistent.json'),
    GrokClient(Path('cache')),
    Path('output/supplemental_test')
)
print(f'Result: {result}')  # Should be None
"
```

### Invalid JSON

```bash
# Create invalid supplemental file
echo '{"invalid": json}' > output/supplemental_test/invalid.json

# Try to enrich (should handle gracefully)
python3 -c "
from pathlib import Path
from src.extraction.supplemental_search import enrich_materials_with_search
from src.grok_client import GrokClient

result = enrich_materials_with_search(
    Path('output/supplemental_test/invalid.json'),
    {'llm_search': True},
    GrokClient(Path('cache'))
)
print(f'Result: {result}')  # Should be 0
"
```

## What to Check

### Phase 1 Output
- [ ] File created in correct directory
- [ ] Materials array populated
- [ ] ULIDs valid and unique
- [ ] Citations parsed correctly
- [ ] Reference types classified
- [ ] JSON schema valid

### Phase 2 Output
- [ ] search_metadata added
- [ ] URLs found and validated
- [ ] availability updated
- [ ] found_via populated
- [ ] search_date recorded

### Phase 3 Output
- [ ] ISBN extracted (post-1966 books)
- [ ] copyright_status added
- [ ] Death dates looked up
- [ ] Copyright logic correct
- [ ] Archive URLs verified

## Troubleshooting

### No materials extracted
- Check event file has citations
- Review logs for extraction errors
- Verify Grok API key configured

### Search not finding URLs
- Check LLM search enabled
- Verify Archive.org API accessible
- Review search_metadata for attempts

### Copyright determination wrong
- Check publication date format
- Verify author death date lookup
- Review jurisdiction setting

### Performance issues
- Check cache directory size
- Verify network connectivity
- Review timeout settings

## Success Criteria

✅ **Phase 1**: Materials extracted with valid structure  
✅ **Phase 2**: URLs found for online materials  
✅ **Phase 3**: Copyright status determined  
✅ **QA**: All code passes pylint, mypy, bandit  
✅ **Schema**: JSON validates against schema  
✅ **Performance**: <40 seconds per chapter  

## Next Steps

After testing passes:
1. Run on full book: `python3 phase2_extract.py`
2. Review output files
3. Spot-check copyright determinations
4. Verify URLs are accessible
5. Document any issues found
