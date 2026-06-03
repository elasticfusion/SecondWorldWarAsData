"""Test equipment image deduplication."""

import pytest


def test_compute_image_hash_same_image(tmp_path):
    """Same image file produces same hash."""
    from src.extraction.equipment import _compute_image_hash

    try:
        from PIL import Image

        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (64, 64), color="red").save(img_path, format="JPEG")

        hash1 = _compute_image_hash(img_path)
        hash2 = _compute_image_hash(img_path)
        assert hash1 is not None
        assert hash1 == hash2
    except ImportError:
        pytest.skip("Pillow not available")


def test_compute_image_hash_different_images(tmp_path):
    """Visually distinct images produce different perceptual hashes."""
    from src.extraction.equipment import _compute_image_hash

    try:
        from PIL import Image, ImageDraw

        path1 = tmp_path / "img1.jpg"
        img1 = Image.new("RGB", (128, 128), color="white")
        ImageDraw.Draw(img1).rectangle([0, 0, 64, 64], fill="black")
        img1.save(path1, format="JPEG")

        path2 = tmp_path / "img2.jpg"
        img2 = Image.new("RGB", (128, 128), color="black")
        ImageDraw.Draw(img2).rectangle([64, 64, 128, 128], fill="white")
        img2.save(path2, format="JPEG")

        hash1 = _compute_image_hash(path1)
        hash2 = _compute_image_hash(path2)
        assert hash1 != hash2
    except ImportError:
        pytest.skip("Pillow not available")


def test_compute_image_hash_invalid_file(tmp_path):
    """Invalid file returns None."""
    from src.extraction.equipment import _compute_image_hash

    bad_file = tmp_path / "not_image.jpg"
    bad_file.write_text("not an image")
    assert _compute_image_hash(bad_file) is None


def test_compute_image_hash_missing_file(tmp_path):
    """Missing file returns None."""
    from src.extraction.equipment import _compute_image_hash

    assert _compute_image_hash(tmp_path / "nonexistent.jpg") is None
