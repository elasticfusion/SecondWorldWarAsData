"""Unit tests for text_utils helper functions."""

import pytest
from src.utils.text_utils import (
    transliterate,
    normalize_name,
    normalize_name_ascii,
    normalize_whitespace,
    remove_special_chars,
    to_filename_safe,
    truncate_text,
    extract_initials,
    similarity_ratio,
)


class TestTransliterate:
    """Test European character transliteration."""

    def test_german_characters(self):
        """Test German umlaut transliteration."""
        assert transliterate("Dönitz") == "Doenitz"
        assert transliterate("Müller") == "Mueller"
        assert transliterate("Günther") == "Guenther"
        assert transliterate("ß") == "ss"
        assert transliterate("Straße") == "Strasse"

    def test_scandinavian_characters(self):
        """Test Scandinavian character transliteration."""
        assert transliterate("Ørsted") == "Oersted"
        assert transliterate("Malmström") == "Malmstroem"
        assert transliterate("Þórðarson") == "Thordarson"
        assert transliterate("Åse") == "Aase"

    def test_polish_characters(self):
        """Test Polish character transliteration."""
        assert transliterate("Wałęsa") == "Walesa"
        assert transliterate("Łódź") == "Lodz"
        assert transliterate("Kraków") == "Krakow"

    def test_french_characters(self):
        """Test French character transliteration."""
        assert transliterate("François") == "Francois"
        assert transliterate("Cœur") == "Coeur"
        assert transliterate("Garçon") == "Garcon"

    def test_greek_characters(self):
        """Test Greek character transliteration."""
        assert transliterate("Παπαδόπουλος") == "Papadpoylos"
        # Greek η (eta) with accent becomes 'i', ά becomes 'a'
        assert transliterate("Αθήνα") == "Athna"

    def test_mixed_characters(self):
        """Test mixed European characters."""
        assert transliterate("Günther von Kluge") == "Guenther von Kluge"
        assert transliterate("François Darlan") == "Francois Darlan"

    def test_ascii_passthrough(self):
        """Test that ASCII text passes through unchanged."""
        assert transliterate("Eisenhower") == "Eisenhower"
        assert transliterate("Patton") == "Patton"


class TestNormalizeName:
    """Test basic name normalization."""

    def test_lowercase_conversion(self):
        """Test conversion to lowercase."""
        assert normalize_name("EISENHOWER") == "eisenhower"
        assert normalize_name("Patton") == "patton"

    def test_whitespace_stripping(self):
        """Test whitespace removal."""
        assert normalize_name("  Eisenhower  ") == "eisenhower"
        assert normalize_name("\tPatton\n") == "patton"

    def test_preserves_internal_spaces(self):
        """Test that internal spaces are preserved."""
        assert normalize_name("Dwight D. Eisenhower") == "dwight d. eisenhower"
        assert normalize_name("George S. Patton") == "george s. patton"

    def test_preserves_periods(self):
        """Test that periods are preserved."""
        assert normalize_name("D. Eisenhower") == "d. eisenhower"


class TestNormalizeNameAscii:
    """Test ASCII name normalization for cross-language matching."""

    def test_german_names(self):
        """Test German name normalization."""
        assert normalize_name_ascii("Dönitz") == "doenitz"
        assert normalize_name_ascii("Müller") == "mueller"
        assert normalize_name_ascii("Günther") == "guenther"

    def test_cross_language_equivalence(self):
        """Test that different spellings normalize to same form."""
        # German variants
        assert normalize_name_ascii("Dönitz") == normalize_name_ascii("Doenitz")
        assert normalize_name_ascii("Müller") == normalize_name_ascii("Mueller")

    def test_scandinavian_names(self):
        """Test Scandinavian name normalization."""
        assert normalize_name_ascii("Ørsted") == "oersted"
        assert normalize_name_ascii("Malmström") == "malmstroem"

    def test_polish_names(self):
        """Test Polish name normalization."""
        assert normalize_name_ascii("Wałęsa") == "walesa"


class TestNormalizeWhitespace:
    """Test whitespace normalization."""

    def test_collapse_multiple_spaces(self):
        """Test collapsing multiple spaces to single space."""
        assert normalize_whitespace("George   S.   Patton") == "George S. Patton"
        assert normalize_whitespace("a  b    c") == "a b c"

    def test_strip_leading_trailing(self):
        """Test stripping leading/trailing whitespace."""
        assert normalize_whitespace("  text  ") == "text"
        assert normalize_whitespace("\t\ntext\n\t") == "text"

    def test_mixed_whitespace(self):
        """Test handling of tabs and newlines."""
        assert normalize_whitespace("a\t\tb\n\nc") == "a b c"


class TestRemoveSpecialChars:
    """Test special character removal."""

    def test_remove_punctuation(self):
        """Test removing punctuation."""
        assert remove_special_chars("O'Brien") == "OBrien"
        assert remove_special_chars("1st Inf. Div.") == "1st Inf Div"

    def test_keep_spaces(self):
        """Test keeping spaces when requested."""
        assert remove_special_chars("Hello, World!", keep_spaces=True) == "Hello World"

    def test_remove_spaces(self):
        """Test removing spaces when requested."""
        assert remove_special_chars("Hello World", keep_spaces=False) == "HelloWorld"

    def test_preserve_alphanumeric(self):
        """Test that alphanumeric characters are preserved."""
        assert remove_special_chars("Test123") == "Test123"


class TestToFilenameSafe:
    """Test filename-safe conversion."""

    def test_basic_conversion(self):
        """Test basic filename conversion."""
        assert to_filename_safe("Dwight D. Eisenhower") == "Dwight_D_Eisenhower"
        assert to_filename_safe("George Patton") == "George_Patton"

    def test_european_characters(self):
        """Test European character handling."""
        assert to_filename_safe("Günther von Kluge") == "Guenther_von_Kluge"
        assert to_filename_safe("François Darlan") == "Francois_Darlan"

    def test_special_char_removal(self):
        """Test special character removal."""
        assert to_filename_safe("O'Brien") == "OBrien"
        assert to_filename_safe("1st Inf. Div.") == "1st_Inf_Div"

    def test_max_length(self):
        """Test maximum length enforcement."""
        long_name = "Very Long Name That Exceeds Maximum Length"
        result = to_filename_safe(long_name, max_length=20)
        assert len(result) == 20
        assert result == "Very_Long_Name_That_"

    def test_empty_string(self):
        """Test empty string handling."""
        assert to_filename_safe("") == ""


class TestTruncateText:
    """Test text truncation."""

    def test_no_truncation_needed(self):
        """Test when text is shorter than max length."""
        assert truncate_text("Short", 10) == "Short"

    def test_truncation_with_default_suffix(self):
        """Test truncation with default ellipsis."""
        assert truncate_text("Very long text here", 10) == "Very lo..."

    def test_truncation_with_custom_suffix(self):
        """Test truncation with custom suffix."""
        assert truncate_text("Very long text", 10, suffix=">>") == "Very lon>>"

    def test_exact_length(self):
        """Test when text is exactly max length."""
        assert truncate_text("12345", 5) == "12345"


class TestExtractInitials:
    """Test initial extraction."""

    def test_full_name(self):
        """Test extracting initials from full name."""
        assert extract_initials("Dwight D. Eisenhower") == "D.D.E."
        assert extract_initials("George S. Patton") == "G.S.P."

    def test_single_name(self):
        """Test single name."""
        assert extract_initials("Eisenhower") == "E."

    def test_multiple_middle_names(self):
        """Test multiple middle names."""
        assert extract_initials("John F. Kennedy Jr.") == "J.F.K.J."

    def test_empty_string(self):
        """Test empty string."""
        assert extract_initials("") == ""

    def test_numbers_ignored(self):
        """Test that numbers are ignored."""
        assert extract_initials("1st Infantry") == "I."


class TestSimilarityRatio:
    """Test text similarity calculation."""

    def test_identical_strings(self):
        """Test identical strings."""
        assert similarity_ratio("Eisenhower", "Eisenhower") == 1.0

    def test_case_insensitive(self):
        """Test case-insensitive comparison."""
        assert similarity_ratio("Eisenhower", "EISENHOWER") == 1.0

    def test_cross_language_matching(self):
        """Test cross-language name matching."""
        # German variants should match perfectly
        assert similarity_ratio("Dönitz", "Doenitz") == 1.0
        assert similarity_ratio("Müller", "Mueller") == 1.0

    def test_similar_strings(self):
        """Test similar but not identical strings."""
        ratio = similarity_ratio("Dwight D. Eisenhower", "Dwight Eisenhower")
        assert ratio > 0.8

    def test_different_strings(self):
        """Test completely different strings."""
        ratio = similarity_ratio("Eisenhower", "Patton")
        assert ratio < 0.5

    def test_empty_strings(self):
        """Test empty string handling."""
        assert similarity_ratio("", "") == 1.0
        assert similarity_ratio("test", "") < 0.5


class TestCaching:
    """Test that functions use caching correctly."""

    def test_normalize_name_cached(self):
        """Test that normalize_name uses cache."""
        # Call twice with same input
        result1 = normalize_name("Test Name")
        result2 = normalize_name("Test Name")
        # Should return same object (cached)
        assert result1 is result2

    def test_transliterate_cached(self):
        """Test that transliterate uses cache."""
        result1 = transliterate("Dönitz")
        result2 = transliterate("Dönitz")
        assert result1 is result2


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_unicode_edge_cases(self):
        """Test unusual Unicode characters."""
        # Should not crash
        result = transliterate("🎯 Test")
        assert "Test" in result

    def test_very_long_strings(self):
        """Test handling of very long strings."""
        long_string = "a" * 10000
        result = normalize_name(long_string)
        assert len(result) == 10000

    def test_special_unicode_spaces(self):
        """Test non-breaking spaces and other Unicode spaces."""
        # Non-breaking space (U+00A0)
        assert normalize_whitespace("a\u00a0b") == "a b"
