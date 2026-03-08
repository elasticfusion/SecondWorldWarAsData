# Solution: Use Grok Structured Outputs API

**Discovery:** Grok has an official **Structured Outputs** feature that guarantees schema-compliant JSON responses.

## Current Problem
- Using prompt-based JSON generation
- Grok truncates responses mid-ULID
- Reports `finish_reason: stop` but JSON is incomplete
- Same issue affects OpenAI, Gemini (known bug pattern)

## Solution: Structured Outputs
From [Grok docs](https://docs.x.ai/docs/guides/structured-outputs):

> "When using structured outputs, the LLM's response is **guaranteed** to match your input schema."

### Implementation
Instead of:
```python
# Current approach - unreliable
response = grok_client.extract_json(prompt, system_prompt)
```

Use:
```python
# Structured Outputs - guaranteed schema compliance
from pydantic import BaseModel
from xai_sdk import Client

class PlaceOutput(BaseModel):
    Event_Name: str
    EventID: str
    Sub_event_Name: str
    Sub_eventID: str
    Place_Mentions: list[PlaceMention]

client = Client(api_key=api_key)
chat = client.chat.create(
    model="grok-beta",
    response_format=PlaceOutput  # ← Guarantees schema compliance
)
response, places = chat.parse(PlaceOutput)
```

### Benefits
1. ✅ **Guaranteed** to match schema (no truncation)
2. ✅ Type-safe Pydantic objects
3. ✅ Automatic validation
4. ✅ No manual JSON parsing

### Next Steps
1. Install xAI SDK: `pip install xai-sdk`
2. Refactor `GrokClient` to support structured outputs
3. Update place extraction to use `response_format`
4. Test with failing files

This should completely eliminate the truncation issue.
