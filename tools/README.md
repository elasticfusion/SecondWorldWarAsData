# Tools

**Last Updated:** 2026-03-13

---

## Overview

Go-based command-line tools for external search integration. These tools provide fast, efficient interfaces to OpenSERP for map and media searches with domain filtering.

---

## Tools

### search_maps

Search for historical maps using OpenSERP with domain filtering.

**Binary:** `tools/search_maps` (compiled Go binary)  
**Source:** `tools/search_maps.go`

**Usage:**
```bash
./tools/search_maps \
  --place "Normandy" \
  --date "1944-06-06" \
  --limit 20 \
  --openserp "http://localhost:7000" \
  --blacklist "domain_blacklist.yaml"
```

**Options:**
- `--place` - Place name to search for (required)
- `--date` - Date in YYYY-MM-DD format (optional)
- `--limit` - Maximum results (default: 20)
- `--openserp` - OpenSERP URL (default: http://localhost:7000)
- `--blacklist` - Path to blacklist YAML (default: domain_blacklist.yaml)

**Output:** JSON array of search results

**Used By:** `src/extraction/openserp_maps.py`

**Features:**
- Domain blacklist filtering
- Domain whitelist support
- Source material path filtering
- Fast JSON output
- OpenSERP integration

---

### search_media

Search for media (images, videos) using OpenSERP with domain filtering.

**Binary:** `tools/search_media` (compiled Go binary)  
**Source:** `tools/search_media.go`

**Usage:**
```bash
./tools/search_media \
  --query "M4 Sherman tank" \
  --limit 20 \
  --openserp "http://localhost:7000" \
  --blacklist "domain_blacklist.yaml"
```

**Options:**
- `--query` - Search query (required)
- `--limit` - Maximum results (default: 20)
- `--openserp` - OpenSERP URL (default: http://localhost:7000)
- `--blacklist` - Path to blacklist YAML (default: domain_blacklist.yaml)

**Output:** JSON array of media items

**Used By:** `src/extraction/equipment.py`

**Features:**
- Media type detection (image, video)
- License extraction
- Domain filtering
- Fast JSON output
- OpenSERP integration

---

### setup_openserp.sh

Setup script for OpenSERP service.

**Usage:**
```bash
./tools/setup_openserp.sh
```

**Actions:**
- Checks for OpenSERP installation
- Starts OpenSERP service
- Configures default port (7000)
- Verifies service is running

**Prerequisites:**
- OpenSERP installed (see openserp/ directory)
- Port 7000 available

---

## Building from Source

### Prerequisites

```bash
# Install Go 1.21+
brew install go  # macOS
# or
apt install golang  # Linux
```

### Build Commands

```bash
# Build search_maps
cd tools
go build -o search_maps search_maps.go

# Build search_media
go build -o search_media search_media.go

# Build both
go build search_maps.go search_media.go
```

### Dependencies

```bash
# Install Go dependencies
go get gopkg.in/yaml.v3
```

---

## Domain Filtering

Both tools use `domain_blacklist.yaml` for filtering:

```yaml
# domain_blacklist.yaml
blacklist:
  - pinterest.com
  - facebook.com
  - instagram.com

whitelist:
  - loc.gov
  - archives.gov
  - wikipedia.org

source_material_paths:
  - /ibiblio.org/
  - /history.army.mil/
```

**Filtering Logic:**
1. Check whitelist first (always allow)
2. Check blacklist (always reject)
3. Check source material paths (allow)
4. Default: allow

---

## Integration

### Python Integration

**OpenSERP Maps:**
```python
import subprocess
import json

result = subprocess.run(
    ["./tools/search_maps", 
     "--place", "Normandy",
     "--date", "1944-06-06",
     "--limit", "20"],
    capture_output=True,
    text=True
)

maps = json.loads(result.stdout)
```

**Equipment Media:**
```python
result = subprocess.run(
    ["./tools/search_media",
     "--query", "M4 Sherman tank",
     "--limit", "20"],
    capture_output=True,
    text=True
)

media = json.loads(result.stdout)
```

---

## Performance

**Why Go?**
- Fast startup time (~1ms vs Python's ~100ms)
- Efficient JSON parsing
- Low memory footprint
- Easy distribution (single binary)

**Benchmarks:**
- Search + filter 100 results: ~50ms
- Python equivalent: ~200ms
- 4x faster for batch operations

---

## Output Format

### search_maps Output

```json
[
  {
    "rank": 1,
    "url": "https://example.com/map.jpg",
    "title": "D-Day Landing Map",
    "description": "Map showing Normandy landings",
    "engine": "google"
  }
]
```

### search_media Output

```json
[
  {
    "media_type": "image",
    "url": "https://example.com/tank.jpg",
    "title": "M4 Sherman Tank",
    "source": "wikipedia.org",
    "license": "Public Domain",
    "description": "Sherman tank in Normandy"
  }
]
```

---

## Troubleshooting

### Binary not found

```bash
# Check if binary exists
ls -la tools/search_maps

# Rebuild if missing
cd tools && go build search_maps.go
```

### OpenSERP connection failed

```bash
# Check OpenSERP is running
curl http://localhost:7000/health

# Start OpenSERP
cd openserp && ./start.sh
```

### Permission denied

```bash
# Make binaries executable
chmod +x tools/search_maps tools/search_media
```

### Go dependencies missing

```bash
# Install dependencies
cd tools
go mod init tools
go get gopkg.in/yaml.v3
go build search_maps.go search_media.go
```

---

## Development

### Modifying Tools

1. Edit `.go` source file
2. Rebuild binary: `go build search_maps.go`
3. Test: `./search_maps --place "Test"`
4. Commit both source and binary

### Adding New Tools

1. Create `new_tool.go` in `tools/`
2. Follow existing pattern (flags, JSON output)
3. Build: `go build new_tool.go`
4. Document in this README
5. Update Python integration code

---

## Related Documentation

- [External Maps](../docs/current/features/external-maps/README.md)
- [Equipment Extraction](../docs/current/features/equipment/MILITARY_EQUIPMENT.md)
- [OpenSERP Integration](../docs/current/features/external-maps/openserp-integration.md)
- [Domain Blacklist](../docs/current/features/external-maps/domain-blacklist.md)

---

## Files

```
tools/
├── README.md (this file)
├── search_maps (binary)
├── search_maps.go (source)
├── search_media (binary)
├── search_media.go (source)
└── setup_openserp.sh (setup script)
```

---

## License

Same as main project (Public Domain).
