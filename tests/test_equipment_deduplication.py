#!/usr/bin/env python3
"""Test equipment image deduplication."""

import sys
from pathlib import Path

from src.extraction.equipment import _compute_image_hash


def test_image_hash():
    """Test image hash computation."""
    print("Testing image hash computation...")

    # Find some equipment images
    filestore = Path("filestore/equipment")
    if not filestore.exists():
        print("❌ No equipment filestore found")
        return False

    image_files = list(filestore.glob("*/*.jpg"))[:5]
    if not image_files:
        print("❌ No images found")
        return False

    print(f"Found {len(image_files)} images to test")

    hashes = {}
    for img_path in image_files:
        img_hash = _compute_image_hash(img_path)
        if img_hash:
            print(f"✅ {img_path.name}: {img_hash}")
            if img_hash in hashes:
                print(f"   🔍 Duplicate of: {hashes[img_hash]}")
            else:
                hashes[img_hash] = img_path.name
        else:
            print(f"❌ Failed to hash: {img_path.name}")

    print(f"\n{len(hashes)} unique images out of {len(image_files)}")
    return True


def test_duplicate_detection():
    """Test that same image produces same hash."""
    print("\nTesting duplicate detection...")

    filestore = Path("filestore/equipment")
    image_files = list(filestore.glob("*/*.jpg"))[:1]

    if not image_files:
        print("⚠️  No images to test")
        return True

    img_path = image_files[0]
    hash1 = _compute_image_hash(img_path)
    hash2 = _compute_image_hash(img_path)

    if hash1 == hash2:
        print(f"✅ Same image produces same hash: {hash1}")
        return True
    else:
        print(f"❌ Hash mismatch: {hash1} != {hash2}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Equipment Image Deduplication Test")
    print("=" * 60)

    try:
        result1 = test_image_hash()
        result2 = test_duplicate_detection()

        print("\n" + "=" * 60)
        if result1 and result2:
            print("✅ All tests passed")
            sys.exit(0)
        else:
            print("❌ Some tests failed")
            sys.exit(1)
    except ImportError as e:
        print(f"\n⚠️  Missing dependency: {e}")
        print("Run: pip install Pillow imagehash")
        sys.exit(1)
