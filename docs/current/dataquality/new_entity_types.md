# New Entity Types: Draft Prompts & Schemas

**Date:** 2026-06-09

---

## 1. Economic Data

### Prompt (`prompts/economics.yaml`)

```yaml
system_prompt: |
  You are a WWII economic data extraction specialist.
  Extract quantitative financial, trade, and production figures from historical texts.

prompt_template: |
  Extract all economic/financial data points from this WWII text.

  Event: {event_name} (ID: {event_id})
  Sub-event: {sub_event_summary} (ID: {sub_event_id})

  Text:
  {text}

  Return JSON matching this structure:
  {schema}

schema: |
  {
    "Economic_Data": [
      {
        "EconomicID": "01ULID...",
        "metric_type": "appropriation",
        "description": "First Lend-Lease Appropriation Act",
        "value": 7000000000,
        "currency": "USD",
        "unit": null,
        "date_context": "March 1941",
        "parties": ["USA", "GBR"],
        "category": "military_aid",
        "subcategory": "lend_lease",
        "direction": "transfer",
        "source_context": "Congressional appropriation for defense aid",
        "original_text": "appropriations of money authorised by Congress... amounted approximately to $14,000 million"
      }
    ]
  }

rules:
  - Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
  - metric_type values: appropriation, expenditure, production_volume, import_volume, export_volume, gold_transfer, debt, cost, price, tonnage, trade_balance
  - category values: military_aid, shipping, munitions, food, raw_materials, financial, industrial_production, trade
  - direction values: transfer, import, export, domestic, bilateral
  - currency: USD, GBP, or null (for non-monetary quantities)
  - unit: tons, ships, aircraft, vehicles, units, barrels, or null (for monetary values)
  - Always extract the numeric value as a number, not a string
  - If a range is given (e.g., "45-50 million"), use the midpoint and note the range in description
  - Country codes use ISO 3166-1 alpha-3
  - If no economic data found, return empty Economic_Data array
```

---

## 2. Policy/Legislation

### Prompt (`prompts/policy.yaml`)

```yaml
system_prompt: |
  You are a WWII policy and legislation extraction specialist.
  Extract named legal instruments, treaties, agreements, and official directives.

prompt_template: |
  Extract all policy instruments, legislation, treaties, and formal agreements from this WWII text.

  Event: {event_name} (ID: {event_id})
  Sub-event: {sub_event_summary} (ID: {sub_event_id})

  Text:
  {text}

  Return JSON matching this structure:
  {schema}

schema: |
  {
    "Policy_Items": [
      {
        "PolicyID": "01ULID...",
        "name": "Lend-Lease Act",
        "official_designation": "H.R. 1776",
        "policy_type": "legislation",
        "date_enacted": "1941-03-11",
        "date_proposed": "1941-01-10",
        "country_of_origin": "USA",
        "parties": ["USA", "GBR"],
        "key_provisions": [
          "Authorizes President to transfer defense articles to any country whose defense is vital to US",
          "Appropriation of $7 billion for initial implementation",
          "Section 3(b): payment or repayment in kind or property"
        ],
        "document_reference": "Cmd. 6311",
        "supersedes": null,
        "status": "enacted",
        "impact_description": "Enabled US military aid to Britain without cash payment",
        "original_text": "An Act to promote the defense of the United States... became the law of the United States on 11th March 1941"
      }
    ]
  }

rules:
  - Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
  - policy_type values: legislation, treaty, agreement, directive, executive_order, white_paper, protocol, armistice, declaration
  - status values: enacted, proposed, ratified, expired, superseded, rejected
  - Country codes use ISO 3166-1 alpha-3
  - parties: list all nations/entities bound by or affected by the instrument
  - key_provisions: extract up to 5 main provisions as brief statements
  - Do NOT extract informal policies or unwritten understandings — only named/numbered formal instruments
  - If no policy items found, return empty Policy_Items array
```

---

## Integration Notes

- Both types would run in Phase 2 extraction, after events/sub-events are identified
- They receive the same `{text}` block as other entity types
- Cross-references: Economic_Data should link to DateMentionIDs where available; Policy_Items should link to bibliography entries for the cited documents
- Storage: `output/economics/` and `output/policy/`
- Dedup key for economics: `(metric_type, value, date_context, category)`
- Dedup key for policy: `(name, date_enacted)`
