"""Tests for externalized prompt loading — verifies render_prompt is called with correct variables."""

import re


def _assert_no_unfilled(prompt: str) -> None:
    """Assert no unfilled {placeholder} variables remain in rendered prompt."""
    unfilled = re.findall(r"\{[a-z_]+\}", prompt)
    assert not unfilled, f"Unfilled placeholders: {unfilled}"


class TestPromptLoading:
    """Verify all externalized prompts load and render without NameError."""

    def test_people_consolidation_prompt(self):
        from src.extraction.people_consolidation import create_consolidation_prompt

        people = [
            {
                "name": "Eisenhower",
                "biographical_profile": {
                    "nationality": "USA",
                    "role_type": "military_leader",
                },
                "event_mentions": [
                    {
                        "position_at_event": "Commander",
                        "original_text": "Gen Eisenhower",
                    }
                ],
            }
        ]
        result = create_consolidation_prompt(people)
        assert result  # Non-empty prompt
        assert "Eisenhower" in result
        _assert_no_unfilled(result)

    def test_bibliography_verify_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "bibliography_verify", verify_prompt="Is this relevant? YES or NO."
        )
        assert "YES" in prompt or "relevant" in prompt
        _assert_no_unfilled(prompt)

    def test_nara_identify_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "nara_identify", verbatim="12th AGp, Ltr of Instrs 5, 17 Aug"
        )
        assert "12th AGp" in prompt
        _assert_no_unfilled(prompt)
        assert "RG" in prompt  # Rules mention Record Groups
        _assert_no_unfilled(prompt)

    def test_nara_verify_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "nara_verify",
            citation="5th Div AAR",
            nara_title="After Action Report",
            nara_description="",
        )
        assert "5th Div AAR" in prompt
        _assert_no_unfilled(prompt)

    def test_equipment_vision_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_vision",
            equipment_name="M4 Sherman",
            equipment_category="armor",
            image_title="Tank photo",
        )
        assert "M4 Sherman" in prompt
        _assert_no_unfilled(prompt)
        assert "armor" in prompt
        _assert_no_unfilled(prompt)

    def test_equipment_enrichment_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_enrichment",
            identifier="M4A3",
            common_name="Sherman",
            category="armor",
            country="USA",
        )
        assert "M4A3" in prompt
        _assert_no_unfilled(prompt)
        assert "Sherman" in prompt
        _assert_no_unfilled(prompt)

    def test_equipment_urls_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_urls",
            equipment_name="P-47 Thunderbolt",
            page_content="<html>wiki content</html>",
        )
        assert "P-47" in prompt
        _assert_no_unfilled(prompt)

    def test_map_search_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "map_search",
            place_name="St. Lo",
            event_context="Breakout",
            date="1944-07-25",
        )
        assert "St. Lo" in prompt
        _assert_no_unfilled(prompt)

    def test_map_vision_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "map_vision",
            place_name="Bastogne",
            event_context="Battle of the Bulge",
            date="1944-12-22",
            title="Tactical map",
        )
        assert "Bastogne" in prompt
        _assert_no_unfilled(prompt)

    def test_license_check_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "license_check",
            url="https://example.com",
            page_content="Public Domain notice",
        )
        assert "Public Domain" in prompt
        _assert_no_unfilled(prompt)

    def test_isbn_lookup_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "isbn_lookup",
            title="Cross-Channel Attack",
            author="Harrison",
            publisher="CMH",
            pub_date="1951",
        )
        assert "Cross-Channel Attack" in prompt
        _assert_no_unfilled(prompt)

    def test_author_death_date_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt("author_death_date", author="Gordon A. Harrison")
        assert "Harrison" in prompt
        _assert_no_unfilled(prompt)

    def test_publication_search_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "publication_search",
            title="The Lorraine Campaign",
            author="Hugh M. Cole",
            doc_type="book",
        )
        assert "Lorraine" in prompt
        _assert_no_unfilled(prompt)

    def test_supplemental_narrative_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "supplemental_narrative",
            event_name="Operation Cobra",
            event_id="01ABC",
            footnote_text="[12] The 2nd Division attacked",
        )
        assert "Operation Cobra" in prompt
        _assert_no_unfilled(prompt)
        assert "2nd Division" in prompt
        _assert_no_unfilled(prompt)

    def test_weather_batch_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "weather_batch",
            event_name="Battle of the Bulge",
            event_id="01XYZ",
            sub_event_blocks="--- Sub-event [01A] ---\nHeavy snow",
        )
        assert "Battle of the Bulge" in prompt
        _assert_no_unfilled(prompt)
        assert "Heavy snow" in prompt
        _assert_no_unfilled(prompt)

    def test_events_batch_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "events_batch", chapter_count="3", chapters_text="Chapter 1 content..."
        )
        assert "3" in prompt
        _assert_no_unfilled(prompt)
        assert "Chapter 1" in prompt
        _assert_no_unfilled(prompt)


class TestGrokSearchMaps:
    """Test grok_search_maps.py uses correct variable names."""

    def test_verify_map_prompt_loads(self):
        """Verify the map vision prompt renders with correct params."""
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "map_vision",
            place_name="Normandy",
            event_context="D-Day",
            date="1944-06-06",
            title="Map of beaches",
        )
        assert "Normandy" in prompt
        _assert_no_unfilled(prompt)
        assert "Map of beaches" in prompt
        _assert_no_unfilled(prompt)


class TestSupplementalSearch:
    """Test supplemental_search.py renders prompt correctly."""

    def test_search_llm_loads_prompt(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "publication_search", title="Test Book", author="Author", doc_type="book"
        )
        assert "Test Book" in prompt
        _assert_no_unfilled(prompt)


class TestBibliographyResolver:
    """Test bibliography_resolver.py prompt rendering."""

    def test_openserp_verify_equipment_prompt(self):
        from src.utils.prompt_loader import render_prompt

        verify_prompt = 'Does this show M4 Sherman? Citation: "M4 Sherman" Title: "Tank photo" URL: http://x'
        prompt = render_prompt("bibliography_verify", verify_prompt=verify_prompt)
        assert "M4 Sherman" in prompt
        _assert_no_unfilled(prompt)
        assert "YES" in prompt or "NO" in prompt  # rules appended
        _assert_no_unfilled(prompt)

    def test_openserp_verify_document_prompt(self):
        from src.utils.prompt_loader import render_prompt

        verify_prompt = 'Does this match? Citation: "5th Div AAR" Title: "After Action" URL: http://x'
        prompt = render_prompt("bibliography_verify", verify_prompt=verify_prompt)
        assert "5th Div AAR" in prompt
        _assert_no_unfilled(prompt)

    def test_url_content_verify_prompt(self):
        from src.utils.prompt_loader import render_prompt

        verify_prompt = (
            'Does this web page match? Citation: "Test" Page content: some text'
        )
        prompt = render_prompt("bibliography_verify", verify_prompt=verify_prompt)
        assert "Test" in prompt
        _assert_no_unfilled(prompt)


class TestEquipmentMedia:
    """Test equipment_ext/media.py prompt rendering."""

    def test_vision_prompt_renders(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_vision",
            equipment_name="P-47 Thunderbolt",
            equipment_category="aircraft",
            image_title="Fighter plane photo",
        )
        assert "P-47 Thunderbolt" in prompt
        _assert_no_unfilled(prompt)
        assert "aircraft" in prompt
        _assert_no_unfilled(prompt)
        assert "Fighter plane photo" in prompt
        _assert_no_unfilled(prompt)

    def test_urls_prompt_renders(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_urls",
            equipment_name="88mm Flak",
            page_content="Wikipedia article about the 88mm gun",
        )
        assert "88mm Flak" in prompt
        _assert_no_unfilled(prompt)


class TestOpenserpMaps:
    """Test openserp_maps.py license check prompt rendering."""

    def test_license_check_prompt_renders(self):
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "license_check",
            url="https://loc.gov/maps/example",
            page_content="This work is in the public domain.",
        )
        assert "loc.gov" in prompt
        _assert_no_unfilled(prompt)
        assert "public domain" in prompt.lower()
        _assert_no_unfilled(prompt)
