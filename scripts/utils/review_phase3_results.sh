#!/bin/bash
# Sync Phase 3 results from S3 and review bibliography resolution quality.
# Usage: bash scripts/review_phase3_results.sh

set -e
ENV="${ENV_NAME:-dev}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
BUCKET="${ENV}-wwii-data-pipeline"
BIB_DIR="output/bibliography"

echo "=== 1. Syncing bibliography from S3 ==="
aws s3 sync "s3://$BUCKET/$BIB_DIR/" "$BIB_DIR/" --region $REGION
echo "  Done: $(ls $BIB_DIR/*.json | wc -l) files"

echo ""
echo "=== 2. Resolution Status Summary ==="
python3 -c "
import json, glob
from collections import Counter

status = Counter()
sources = Counter()
total = 0

for f in glob.glob('$BIB_DIR/*.json'):
    try:
        d = json.load(open(f))
        if not isinstance(d, dict): continue
        total += 1
        s = d.get('search_status', 'unknown')
        status[s] += 1
        if s == 'resolved':
            sources[d.get('search_source', 'unknown')] += 1
    except: pass

print(f'Total entries: {total}')
print()
print('Status:')
for s, c in status.most_common():
    print(f'  {s:20s} {c:6d} ({c/total*100:.1f}%)')
print()
print('Resolved by source:')
for s, c in sources.most_common():
    print(f'  {s:20s} {c:6d}')
"

echo ""
echo "=== 3. Sample Resolved (NARA) ==="
python3 -c "
import json, glob
shown = 0
for f in sorted(glob.glob('$BIB_DIR/*.json')):
    try:
        d = json.load(open(f))
        if not isinstance(d, dict): continue
        if d.get('search_source') != 'nara_catalog': continue
        urls = d.get('resource_urls', [])
        title = d.get('title', '?')[:60]
        print(f'  {title}')
        print(f'    → {urls[0] if urls else \"no url\"}')
        shown += 1
        if shown >= 5: break
    except: pass
if not shown: print('  (none found)')
"

echo ""
echo "=== 4. Sample Resolved (Archive.org) ==="
python3 -c "
import json, glob
shown = 0
for f in sorted(glob.glob('$BIB_DIR/*.json')):
    try:
        d = json.load(open(f))
        if not isinstance(d, dict): continue
        if d.get('search_source') != 'archive_org': continue
        urls = d.get('resource_urls', [])
        title = d.get('title', '?')[:60]
        print(f'  {title}')
        print(f'    → {urls[0] if urls else \"no url\"}')
        shown += 1
        if shown >= 5: break
    except: pass
if not shown: print('  (none found)')
"

echo ""
echo "=== 5. Verification Check (spot check 3 NARA URLs) ==="
python3 -c "
import json, glob, subprocess
checked = 0
for f in sorted(glob.glob('$BIB_DIR/*.json')):
    try:
        d = json.load(open(f))
        if not isinstance(d, dict): continue
        if d.get('search_source') != 'nara_catalog': continue
        urls = d.get('resource_urls', [])
        if not urls: continue
        url = urls[0]
        result = subprocess.run(['curl', '-sI', '-o', '/dev/null', '-w', '%{http_code}', url], capture_output=True, text=True, timeout=10)
        code = result.stdout.strip()
        title = d.get('title', '?')[:50]
        status = '✓' if code == '200' else f'✗ ({code})'
        print(f'  {status} {title} → {url}')
        checked += 1
        if checked >= 3: break
    except: pass
if not checked: print('  (no NARA URLs to check)')
"

echo ""
echo "=== Done ==="
