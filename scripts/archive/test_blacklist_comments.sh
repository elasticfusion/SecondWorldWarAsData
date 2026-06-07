#!/bin/bash
# Test script to demonstrate blacklist comment functionality
set -euo pipefail

echo "Testing blacklist comment feature..."
echo ""

# Create a test blacklist
cat > test_blacklist.yaml << 'EOF'
blacklist:
- pinterest.com
- youtube.com
source_material_paths:
- ibiblio.org/hyperwar/
EOF

echo "Initial blacklist:"
cat test_blacklist.yaml
echo ""
echo "---"
echo ""

# Create mock search results with blacklisted URLs
cat > mock_results.json << 'EOF'
[
  {
    "rank": 1,
    "url": "https://www.pinterest.com/pin/normandy-map/",
    "title": "Normandy D-Day Map",
    "description": "Historical map of Normandy invasion",
    "engine": "google"
  },
  {
    "rank": 2,
    "url": "https://www.youtube.com/watch?v=normandy",
    "title": "Normandy Map Video",
    "description": "Video showing Normandy map",
    "engine": "bing"
  },
  {
    "rank": 3,
    "url": "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/normandy.jpg",
    "title": "Normandy Tactical Map",
    "description": "Official US Army map",
    "engine": "duckduckgo"
  }
]
EOF

echo "When search_maps processes these URLs, it will append comments like:"
echo ""
echo "# Filtered: https://www.pinterest.com/pin/normandy-map/ (blacklisted domain: pinterest.com)"
echo "# Filtered: https://www.youtube.com/watch?v=normandy (blacklisted domain: youtube.com)"
echo "# Filtered: https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/normandy.jpg (source material: ibiblio.org/hyperwar/)"
echo ""
echo "These comments will be automatically appended to domain_blacklist.yaml"
echo "whenever a URL is filtered out during the search process."

# Cleanup
rm -f test_blacklist.yaml mock_results.json
