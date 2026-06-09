# 7-Minute Speech Outline: Second World War as Data

**Target:** ~7 minutes (~1,050 words spoken at natural pace)

---

## 1. Opening Hook (45 seconds)

**[VISUAL: Map of the Second World War — showing all theaters/fronts globally]**
https://i.etsystatic.com/28845484/r/il/c25a56/3411596423/il_fullxfull.3411596423_17ef.jpg

- Start with scope: "The Second World War was the largest global conflict in human history — fought across six continents, involving dozens of nations, over six years. From the beaches of Normandy to the islands of the Pacific, from the Eastern Front to North Africa, from the Arctic convoys to the Burma campaign. The documentation is staggering — millions of pages of after-action reports, government records, and scholarship spread across archives worldwide."
- The problem: All of that knowledge is locked in narrative text, manually typed lists, and participant accounts of mixed quality. You can read it, but you can't query it, cross-reference it, or compute with it.
- Current focus & vision: "We've started with the campaign in Northwest Europe and US-based documents — beginning with the public domain US Army Green Books — but the goal is global. The Pacific theater, the Eastern Front from Stalingrad to Berlin, Allied and Axis records from every nation. All of it, structured and connected."
- Thesis: "We're building the infrastructure to turn the entire war into data."

---

## 2. The Problem (1 minute)

**[VISUAL: US Army Hyperwar archive — example of unstructured source material]**
[HyperWar: US Military History](https://www.ibiblio.org/hyperwar/USA/)

- WWII is the most documented conflict in history — millions of pages of after-action reports, government records, memoirs, and scholarship
- It's almost entirely unstructured — prose in books, PDFs in archives, scanned documents at national archives around the world, and hand-written documents
- Researchers spend enormous time manually connecting dots across sources
- No machine-readable, cross-referenced dataset exists at scale
- Example: "If you want to know every engagement a specific division fought in, ordered by date, with weather and casualty data — that query is currently impossible without weeks of manual research"
- Broader applicability: While currently focused on WWII, the pipeline architecture could be adapted to any historical period — the same entity extraction approach applies to the Napoleonic Wars, WWI, or any conflict with documentary records

---

## 3. What We Built (1.5 minutes)

**[VISUAL: GitHub repository]**
[SecondWorldWarAsData](https://github.com/elasticfusion/SecondWorldWarAsData)

- An AI-powered pipeline that reads historical documents and extracts structured entities
- Four phases:
  - **Pre-parse**: Conversion of PDF, HTML, EPUB, and other formats into markdown
  - **Parse**: Source documents converted using AI from  (markdown) → structured JSON
  - **Extract**: AI identifies 11 entity types — events, groups (military units, government departments, companies, political parties), places, dates, equipment, weather, logistics, casualties, maps, citations
  - **Enrich**: Cross-references with Wikipedia, national archives (NARA, The National Archives UK, Bundesarchiv, Central Archives of Ukraine, Russian State Military Archive), weather records
- Collecting references from national archives and online sources allows us to pull primary source data back into the overall system, expanding the dataset organically as enrichment discovers new material
- Every entity gets a unique ID (ULID) — full cross-referencing
- Example output: An event like "Operation Overlord" links to its sub-events, which link to specific people, places, dates, units, and weather conditions
- "You can follow a thread from a battle → to a commander → to every other engagement they led → to the units involved → to the equipment those units had"

---

## 4. How the AI Works (1 minute)

**[VISUAL: Cross-Reference Diagram]**
cross_reference_diagram.png

- Uses the Grok large language model via batch API
- Carefully engineered prompts for each of the 11 entity types
- Automatic deduplication — AI suggests merges, human reviews
- Not hallucinating data — extracting from source documents with citations
- Strict source tracking — every extracted entity traces back to its original document, page, and passage, ensuring provenance and verifiability
- Batch processing at 50% cost savings
- 332 automated tests ensure output quality and consistency
- All code was created with AI

---

## 5. Why It Matters — Who Benefits (1.5 minutes)

**[VISUAL: UK Military History archive — showing international scope]**
[HyperWar: UK Military History](https://www.ibiblio.org/hyperwar/UN/UK/index.html)

- **Researchers & Historians**: Query the war as a dataset. Find patterns across theaters, time periods, units. Answer questions that would take months of manual work.
- **Educators**: Build interactive timelines, map visualizations, data-driven lesson plans. Students can explore connections themselves.
- **Developers & Data Scientists**: Build applications on top of structured WWII data — visualizations, simulations, analytical tools.
- **Archivists & Librarians**: Connect disparate collections through shared entity references. A document in Washington links to the same person entity as a memoir in London, a unit record in Berlin, or a battle report in Kyiv.
- **The Public**: This history belongs to everyone. The output is freely available and openly licensed.

---

## 6. Current Status & What's Next (45 seconds)

**[VISUAL: AWS S3 output bucket — live data pipeline results]**
[S3: dev-wwii-data-pipeline/output](https://us-east-1.console.aws.amazon.com/s3/buckets/dev-wwii-data-pipeline?region=us-east-1&prefix=output/&showversions=false)

- Production-ready: Events, Dates, Places, People, Groups, Maps, Citations
- Experimental: Weather, Equipment, Logistics, Casualties
- Runs locally (any developer can contribute) or on AWS at scale
- AWS allows us to process massive document collections in parallel, scale on demand, and share the resulting datasets globally through cloud-native services
- Next steps: More source material, more entity types, a public API, visualization layer
- Future: Leverage AI/Grok to translate non-English documents, unlocking Axis records and Allied nations' archives in their original languages
- Open source and openly licensed — contributions welcome

---

## 7. Closing (30 seconds)

- Return to the global scope: "We started with Northwest Europe and US records, but this war was global — and so is this project's ambition. Every theater, every nation's records, all connected."
- The broader vision: "History shouldn't be locked in paragraphs. When it becomes data, it becomes queryable, connectable, and alive in ways narrative alone cannot achieve."
- Call to action: Invite collaboration — historians, developers, data scientists, anyone who believes the past deserves to be computationally accessible.

---

## Speaker Notes

- **Pace**: ~150 words/minute. Don't rush the examples.
- **Visual aid**: If slides available, show one JSON entity example and one cross-reference diagram.
- **Audience adjustment**: For technical audiences, expand Section 4. For humanities audiences, expand Section 5.
- **Q&A prep**: Expect questions about AI accuracy, source selection, and how this differs from existing WWII databases.
