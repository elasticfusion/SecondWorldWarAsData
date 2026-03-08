# Requirements Document

## Requirements

### Introduction

This specification defines an automated workflow approach to analyzing and extending historical documents. The system will use a mix of AI resources to accomplish this.

- **AWS Bedrock Claude Sonnet 4.5**: for Python code generation and logic.
- **Grok AI** for natural language requests for revising and extending the historical documents by including and incorporating both online resources and the discovery of offline documents.

### main_components:

- prepares review directory structure
- copies content.md files
- assembles prompt templates with placeholders

### content requirements:

- extracts mentions
  - events mentions
    - include sub-events 
      - sub-events shall have a summary
      - Preserve sub-event-fulltext:
        - capture the original text used for the sub-event analysis.
          - must be able to be able to track the the source of the material
          - Sub-event_fulltext.Paragraph_[nn] (for example, it should document the absolute paragraph for all paragraphs in the entire $SourceLink and include all text used in analysis.
              - Each paragraph should have its own unique paragraph number.
              - The purpose of the paragraph number is to allow the user to easily identify the paragraph in the original source material by humans
    - By default, the language is English, if the source material is in another language, it shall be noted.  
  - depending on the event/sub-event:
    - dates mentions
      - shall include the event name
      - shall include the sub-event name
      - shall include event and sub-event ULIDs 
      - Shall include a ULID for each entry
      - Capture the time/date or time/date range as presented in the text. 
		- Occasionally an approximate time will be mentioned, include it with the date.
		- Occasionally an exact time will be mentioned, include it with the date.
        - Occasionally a date range will be mentioned (hours, days, weeks, months, years)
        - Note the source of the material, (e.g allied implying British times, German, implying german times or if specified Universal/Zulu time)
    - place mentions
      - shall include event and sub-event ULIDs
      - shall include the event name
      - shall include the sub-event name
      - Shall include a ULID for each entry
      - geography mentions
       - When there is no context for the place, use the geographical center
      - latitude/longitude coordinates
      - name
       - current name
       - historical name
      - bounding box coordinates at 100 kilometers
      - dates with context
      - Allows for multiple references "from place1 to place2 to place3" 
      - the default language is English, if the source material is in another language, it shall be noted. 
      - place names shall support, international character sets
   - weather mentions
      - shall include event and sub-event ULIDs
      - shall include sub-event weather descriptions
        - if there was a notable impact mentioned in the source data, this should be included.
        - Note the unit of measurements used (farenheit, imperial, metric)
      - shall include the event name
      - shall include the sub-event name
      - Shall in include place name
      - shall include place ULIDs
      - shall include date mentions
      - shall include date ULIDs
      - Each entry shall include a ULID.
      - API resources to retrieve weather data relevant to the date of the event
      - Shall include a reference to image supporting data, if relevant
      - Shall include supporting material image ULIDs, if relevant
    - people mentions
      - people mentions shall be centrally managed and data appended to it. specific events or sub-events shall be appended to the central people mentions file.
      - shall include all event names
      - shall include all sub-event names
      - Shall include a ULID for each entry
      - creates a biographical profile
        - military leaders mentioned
        - political leaders mentioned
        - military personnel mentioned, especially those who win specific military awards including, but limited to:
            - Medal of Honor
            - Distinguished Service Medal
            - Iron Cross
            - Victoria Cross
        - notes current position
        - notes biographical details
        - notes life events
      - updates are tied to a specific date(s) of the event/sub-event and should be included in the update.
      - shall include event and sub-event ULIDs
      - the default language is English, if the source material is in another language, it shall be noted. 
      - people names shall support, international character sets
    - people grouping mentions
      - people groupings mentions shall be centrally managed and data appended to it. specific events or sub-events shall be appended to the central people mentions file.
      - shall include all event names
      - shall include all sub-event names
      - Declare the type of group, not limited to types below
      - types:
       - countries
       - alliances of countries or other political sub-groups
       - military units
         - Should attempt to identify high organizational units such as:
           - squads to platoons to battalions to regiments to divisions to corps to armies
           - at the date of the event
       - political parties
       - government organizations
         - including anti-government organizations
       - religious organizations
         - including major factions
         - dissident factions
      - include country of origin
      - include alliance membership if relevant  
- supporting material:
    - maps
      - shall include the event name
      - shall include the sub-event name
      - shall include event and sub-event ULIDs
      - Shall include a ULID for each entry
      - associated with the source material
        - shall include event and sub-event ULIDs
      - third party maps relevant to the date of the event
        - URL of the map
        - and local copy of the map
        - date of the map URL capture
        - license information for all maps
        - shall be related to the place and date of the event
    - images
     - content-based
      - shall include the event name
      - shall include the sub-event name
      - Shall include a ULID for each entry
      - shall include event and sub-event ULIDs
       - images associated with the event/sub-event from the source material
       - images from external resources with relevance by date and location
       - shall include event and sub-event ULIDs
      - online resources
        - shall include the event name
        - shall include the sub-event name
        - Shall include a ULID for each entry
        - shall include event and sub-event ULIDs
        - URL of the image or moving image
        - and local copy of the image
        - date of the image URL capture
        - license information for all images 
      - offline resources
        - offline resources shall be, with minimal changes, be able to be converted to online resources
        - shall include the event name
        - shall include the sub-event name
        - shall include event and sub-event ULIDs
        - Shall include a ULID for each entry
        - location of the image repository, archive, library, book, etc.
        - Physical location including address, city, state, country
        - specific information useful for identifying the image in the repository
     

- supplemental Material
  - supplemental material is defined as references made by the author to other sources of information:
    - endnotes
    - footnotes
    - bibliography
  - each type of supplemental materia shall be clearly labeled as to reference type
  - shall include event and sub-event ULIDs
  - Shall include a ULID for each entry
  - date or date range tied to reference, if applicable
  - verbatim original source material reference
    - online resources
        - URL of the image or moving image
        - date of the image URL capture
        - license information for all images 
        - online supplemental material shall be cached locally for future reference.
    - offline resources
          - location of the material, such as archive, library, book, etc...
          - physical location of material including address, city, state, country
          - specific information useful for identifying the image in the repository
          - offline resources shall be easily converted to online resources

### technical requirements:

- implements cache lookups / API call with retry logic for all types of mentions
  - Allows for secondary API calls to be made if the primary API call fails.
  - Secondary API calls may be made to different sources.
- saves results as JSON
  - each event and sub-event will have a unique ID based on ULID
  - Each mention type shall be treated as a separate API request
    - Each mention type may have logic for:
        - secondary API call logic
        - tertiary API calls to different sources
        - The API session shall be preserved in all cases.
  - the JSON for each type of mention shall:
    - have a defined standard for each sort of mention 
    - be compliant with a standard
    - shall validate against the standard schema before writing to disk making local changes, with Python, or recalling the API if required
      - Make an API request suggesting code changes to add local validation logic
    - include the original source material reference
  - include the date of the event
  - include the contextual information extracted from the source material
    - creates a cache for all mention types


### processing components:
- High level requirements:
  - Written in Python unless otherwise requested.
  - Stores all results in pre-defined JSON structure
- Local requirements:
  - Paths to be defined in a configuration file
  - Identify and review directory structure of the source material
    - for book-based materials:
      - the general structure will be either:
      - chapterNN.md
      - chapterXX-sectionN.md
    - for website-based materials:
      - original URL
      - original source material
      - conversion to markdown
  - copies source material, to processing location.
  - assembles markdown prompt templates with placeholders
    - prompt templates shall be be defined in a configuration file
  - extracts place mentions from chapterXX-section-event.json
  - implements cache lookup / API call with retry logic
  - saves results as chapterXX-section-TYPE.json
  - creates a local jq query to validate ULID validity
  - Cache locations shall be defined in a configuration file
    - local file systems will be the default storage location
    - S3 buckets may be used as an alternative


key_functions:
  - central_config: for environment variables and configuration file paths
  - setup_logging: Initializes console + optional rotating file logger
  - get_paths: Computes file paths for a given chapter/section/source material
  - create_review_directory: Ensures review subfolder exists
  - copy_content_file: Copies markdown content into review folder
  - assemble_target_yaml: Concatenates review.yaml + type description + JSON structure YAML
    - including path information for each component
    - replace_placeholders: Substitutes book/chapter/filename tokens in prompt YAML
      - also defined in configuration file
  - load_mentions: Parses sub-events from JSON → returns list of dicts with context & mentions
  - send_api_request: Sends prompt to https://api.x.ai/v1/chat/completions with retry on 5xx
  - process_places: Orchestrates cache check → API call → JSON cleaning → result storage
    - storage location: determined by configuration settings
  - main: Argument parsing, folder discovery, per-chapter-section processing loop


## Glossary

- **mention**: A term or phrase that is recognized as referring to a specific entity or concept within a sub-event.
- **event**: usually the focus of a chapter
- **sub-event**: grouping of paragraphs into logical subsections based upon The content in the "source"

**User Story:** 

### Requirement 1: JSON creation and Validation

As a data administrator, I want to depend on consistent JSON data formats, so that I can easily integrate data into other systems.

#### Acceptance Criteria

1. WHEN a new event/sub-event is created, THE data shall be displayed JSON only
2. WHEN a JSON is created, THE JSON shall be compliant with a standard schema

### Requirement 2: ULID Validation

As a data administrator, I want to ensure that all ULIDs used in the system are valid and correctly formatted, so that I can maintain data integrity and consistency.

#### Acceptance Criteria

1. WHEN an event is created, THE event shall have a ULID
2. WHEN a sub-event is created, THE sub-event shall have a ULID
3. WHEN a mention or supporting material is added, THE entry shall reference the event and sub-event ULIDs.
4. WHEN a ULID is defined in a mention, The ULID shall be validated.

### Requirement 3: Event and Sub-event Review

As a content reviewer, I want to review events and sub-events groupings and summaries based upon the mentions found in the source material.

#### Acceptance Criteria

1. WHEN a summary is created, A corresponding JQ-based script will be created to make the Paragraphs from the source material and it's summary human readable.

### Requirement 4: As a content reviewer, I want to be able to validate mentions against the source material with a JQ-based script.

#### Acceptance Criteria

1. WHEN a mention is created, A corresponding JQ query will be created to validate the mention against the source material.
2. WHEN a mention is validated, THE validation result shall be displayed in a human-readable format.

### Requirement 5: As a content reviewer, when data is retrieved from the internet, I need to be able to validate and document it and determine its license, including the lack of license.

#### Acceptance Criteria

1. WHEN supporting material is added, THE system will generate a script that performs a CURL command that will retrieve license information from the source material site.

### Requirement 6: As a content reviewer, I want to be able to download supporting material from the internet.

#### Acceptance Criteria

1. WHEN supporting material is added, create a script that downloads the material from the source material site. 
2. WHEN the scripts is run, THE system will place it in a cache.

### Requirement 7: As a programmer, I want all code to be written in Python to be put through quality assurance utilities.

#### Acceptance Criteria

1. WHEN code is written, THE system should be reviewed by pylint, radon, bandit, and mypy.
2. AFTER review, THE system should remediate all CRITICAL and HIGH issues.


