#!/usr/bin/env python3
"""
Render Mermaid diagrams from markdown files to PNG.

Extracts ```mermaid blocks, hashes their content, and only re-renders
when the diagram has changed. Outputs a manifest mapping diagram names
to their content hashes so PNGs stay correlated with source.

Usage:
    python3 scripts/render_mermaid_diagrams.py [markdown_file ...]

    If no files given, processes docs/current/core/WORKFLOW_DIAGRAMS.md

Needs node/npx (it runs @mermaid-js/mermaid-cli). Where the OS blocks Chrome's sandbox
(Ubuntu 23.10+ AppArmor unprivileged-userns restriction), set MERMAID_NO_SANDBOX=1: the
headless browser then runs unsandboxed - use it only for this repo's own diagram files (no web
content; the renderer version is pinned, MERMAID_CLI). Exit 1 when any diagram failed to render.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MANIFEST_NAME = "mermaid_manifest.json"
# pinned: the renderer runs in a headless Chrome (unsandboxed with MERMAID_NO_SANDBOX=1)
MERMAID_CLI = "@mermaid-js/mermaid-cli@12.2.0"


def extract_diagrams(md_path: Path) -> list[tuple[str, str]]:
    """Extract (heading, mermaid_source) pairs from a markdown file."""
    text = md_path.read_text(encoding="utf-8")
    # Find all mermaid blocks with the heading that precedes them
    diagrams = []
    # Split into sections by ## headings
    heading = "untitled"
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line.lstrip("# ").strip()

    # Better approach: find each mermaid block and look back for nearest heading
    blocks = list(re.finditer(r"```mermaid\n(.*?)```", text, re.DOTALL))
    for block in blocks:
        # Find the nearest ## heading before this block
        before = text[: block.start()]
        headings = re.findall(r"^##\s+(.+)$", before, re.MULTILINE)
        name = headings[-1] if headings else "untitled"
        diagrams.append((name, block.group(1).strip()))
    return diagrams


def slug(name: str) -> str:
    """Convert heading to filename slug."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def content_hash(source: str) -> str:
    """Short hash of diagram source for change detection."""
    return hashlib.sha256(source.encode()).hexdigest()[:12]


def _puppeteer_config() -> "str | None":
    """A puppeteer config file disabling Chrome's sandbox when MERMAID_NO_SANDBOX=1, else None."""
    if os.environ.get("MERMAID_NO_SANDBOX") != "1":
        return None
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump({"args": ["--no-sandbox", "--disable-setuid-sandbox"]}, f)
        return f.name


def render_diagram(source: str, output_png: Path) -> bool:
    """Render a mermaid diagram to PNG via mmdc. Returns True on success."""
    with tempfile.NamedTemporaryFile(suffix=".mmd", mode="w", delete=False) as f:
        f.write(source)
        mmd_path = f.name
    config = _puppeteer_config()
    try:
        cmd = ["npx", "--yes", MERMAID_CLI, "-i", mmd_path,
               "-o", str(output_png), "-b", "white", "-s", "2"]  # fmt: skip
        if config:
            cmd += ["-p", config]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
            return False
        return True
    finally:
        Path(mmd_path).unlink(missing_ok=True)
        if config:
            Path(config).unlink(missing_ok=True)


def main():
    base = Path(__file__).resolve().parent.parent
    default_files = [base / "docs" / "current" / "core" / "WORKFLOW_DIAGRAMS.md"]

    md_files = [Path(f) for f in sys.argv[1:]] if len(sys.argv) > 1 else default_files

    failed = 0
    for md_path in md_files:
        if not md_path.exists():
            print(f"File not found: {md_path}", file=sys.stderr)
            continue

        # Output dir: sibling images/ directory
        images_dir = md_path.parent / "images"
        images_dir.mkdir(exist_ok=True)

        # Load existing manifest
        manifest_path = images_dir / MANIFEST_NAME
        manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        )

        diagrams = extract_diagrams(md_path)
        print(f"{md_path.name}: {len(diagrams)} diagram(s)")

        updated = unchanged = 0
        for name, source in diagrams:
            filename = slug(name)
            h = content_hash(source)
            png_name = f"{filename}.png"
            png_path = images_dir / png_name

            # Skip if hash unchanged and PNG exists
            if manifest.get(filename, {}).get("hash") == h and png_path.exists():
                print(f"  ✓ {png_name} (unchanged)")
                unchanged += 1
                continue

            print(f"  Rendering {png_name} ...", end=" ", flush=True)
            if render_diagram(source, png_path):
                manifest[filename] = {
                    "heading": name,
                    "hash": h,
                    "file": png_name,
                    "source": md_path.name,
                }
                updated += 1
                print("done")
            else:
                failed += 1
                print("FAILED")

        # Save manifest
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  {updated} updated, {unchanged} unchanged, {failed} failed")
        print(f"  Manifest: {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
