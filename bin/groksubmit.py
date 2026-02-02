#!/usr/bin/env python3
"""
Module for submitting prompts to the Grok API based on configured variables.

This script assembles a prompt from YAML files, performs placeholder replacements,
attaches local source content, and sends the request to the API. Results are saved
to an output JSON file.
"""

import sys
import os
import shutil
import requests
import json  # Added for parsing local source JSON
import time  # Added for timing measurements
import logging  # Added for structured logging
import argparse  # Added for command-line argument parsing
from logging.handlers import RotatingFileHandler  # For log rotation

import cache_handler  # Import the new caching submodule
import directory_scanner  # Import the directory scanner submodule

from config import (
    API_KEY, MODEL, BOOK, BOOKNAME, TYPE
)

# Default log file name
LOG_FILE_NAME = "groksubmit.log"


def setup_logging(log_dir=None):
    """Set up logging with optional file output in the specified directory."""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Optional file handler with rotation
    if log_dir:
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, LOG_FILE_NAME)
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)  # 10 MB max, 5 backups
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info("Logging to file: %s", log_file)

    return logger


def print_environment_info(logger):
    """Print diagnostic information about the Python environment."""
    logger.info("Using Python executable: %s", sys.executable)
    logger.info("Python version: %s", sys.version)


def create_review_directory(chapter_folder, review_folder, logger):
    """Create the review directory if it does not exist."""
    logger.info("Creating review directory: %s/%s", chapter_folder, review_folder)
    try:
        os.makedirs(os.path.join(chapter_folder, review_folder), exist_ok=True)
    except Exception as e:
        logger.error("Failed to create review directory: %s", e)
        sys.exit(1)


def copy_content_file(chapter, section, book, chapter_folder, review_folder, logger):
    """Copy the content.md file to the review folder."""
    content_src = os.path.join(f"../{book}/data/prompts/chapter{chapter}", f"chapter{chapter}{section}-content.md")
    content_dst = os.path.join(chapter_folder, review_folder, f"chapter{chapter}{section}-content.md")
    logger.info("Copying content file from %s to %s", content_src, content_dst)
    try:
        shutil.copy(content_src, content_dst)
    except FileNotFoundError as e:
        logger.error("Content source file not found: %s", e)
        sys.exit(1)
    except IOError as e:
        logger.error("I/O error during content file copy: %s", e)
        sys.exit(1)


def initialize_output_json(chapter_folder, review_folder, output_file, logger):
    """Create or touch the output JSON file."""
    json_file = os.path.join(chapter_folder, review_folder, output_file)
    logger.info("Initializing output JSON file: %s", json_file)
    try:
        with open(json_file, 'a'):
            pass
    except IOError as e:
        logger.error("I/O error initializing output JSON: %s", e)
        sys.exit(1)
    return json_file


def assemble_target_yaml(book, type_val, chapter_folder, review_folder, target_file, logger):
    """Assemble the target YAML file by concatenating source YAML files."""
    review_yaml = f"../{book}/data/prompts/review.yaml"
    type_desc_yaml = f"../{book}/data/prompts/{type_val}_description_of_data.yaml"
    json_struct_yaml = f"../{book}/data/prompts/json-structure-{type_val}.yaml"
    target_path = os.path.join(chapter_folder, review_folder, target_file)

    logger.info("Assembling target YAML at %s", target_path)
    try:
        with open(target_path, 'w') as target:
            for yaml_file in [review_yaml, type_desc_yaml, json_struct_yaml]:
                logger.debug("Reading YAML source: %s", yaml_file)
                with open(yaml_file, 'r') as src:
                    target.write(src.read())
    except FileNotFoundError as e:
        logger.error("YAML source file not found: %s", e)
        sys.exit(1)
    except IOError as e:
        logger.error("I/O issue during YAML assembly: %s", e)
        sys.exit(1)

    return target_path


def replace_placeholders(target_path, bookname, chapter, section, localsource, logger):
    """Replace placeholders in the target YAML file."""
    logger.info("Replacing placeholders in %s", target_path)
    try:
        with open(target_path, 'r') as f:
            content = f.read()

        content = content.replace("#bookname#", bookname)
        content = content.replace("#chapter#", str(chapter))
        content = content.replace("#localsource#", localsource)
        content = content.replace("chapter1-meta.md", f"chapter{chapter}{section}-content.md")

        with open(target_path, 'w') as f:
            f.write(content)
    except FileNotFoundError as e:
        logger.error("Target file not found for replacement: %s", e)
        sys.exit(1)
    except IOError as e:
        logger.error("I/O issue during replacement: %s", e)
        sys.exit(1)


def clean_up_description_file(chapter_folder, review_folder, type_val, logger):
    """Remove the type description YAML if it exists in the review folder."""
    desc_in_folder = os.path.join(chapter_folder, review_folder, f"{type_val}_description_of_data.yaml")
    if os.path.exists(desc_in_folder):
        logger.info("Removing description file: %s", desc_in_folder)
        try:
            os.remove(desc_in_folder)
        except OSError as e:
            logger.error("Unable to remove description file: %s", e)


def load_base_prompt(target_path, logger):
    """Load the base prompt from the target file."""
    logger.info("Loading base prompt from %s", target_path)
    try:
        with open(target_path, 'r') as f:
            return f.read()
    except FileNotFoundError as e:
        logger.error("Target prompt file not found: %s", e)
        sys.exit(1)
    except IOError as e:
        logger.error("I/O issue loading prompt: %s", e)
        sys.exit(1)


def load_places(chapter_folder, review_folder, localsource, logger):
    """Load and parse places from sub-events in the local source JSON file."""
    source_path = os.path.join(chapter_folder, review_folder, localsource)
    logger.info("Loading local source from: %s", source_path)
    try:
        with open(source_path, 'r') as f:
            data = json.load(f)
        logger.info("Loaded data keys: %s", list(data.keys()))
        # Handle both 'Sub-events' and 'Sub-event' keys
        sub_events_data = data.get('Sub-events', data.get('Sub-event', []))
        logger.info("Number of sub-events: %d", len(sub_events_data))
        places_list = []
        for sub_index, stanza in enumerate(sub_events_data, start=1):
            if not isinstance(stanza, dict):
                logger.warning("Sub-event %d: Skipping non-dict stanza of type %s", sub_index, type(stanza))
                continue
            summary = stanza.get('Sub-event_summary', '')
            sub_places = stanza.get('Sub-Event-Places', [])
            logger.info("Sub-event %d: %d places found", sub_index, len(sub_places))
            for place_index, place in enumerate(sub_places, start=1):
                places_list.append({
                    'sub_event_index': sub_index,
                    'place_index': place_index,
                    'place': place.strip(),  # Trim leading/trailing whitespace
                    'summary_context': summary
                })
        logger.info("Total places extracted: %d", len(places_list))
        return places_list
    except FileNotFoundError:
        logger.error("File '%s' not found. Ensure the file exists or update the path in config.py.", source_path)
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in local source: %s", e)
        sys.exit(1)
    except IOError as e:
        logger.error("I/O issue loading local source: %s", e)
        sys.exit(1)


def send_api_request(base_prompt, place_data, logger):
    """Send a request to the Grok API for a single place and return the response, with retry for server errors."""
    # Create a concise prompt for this place, emphasizing flat JSON structure
    place_prompt = f"{base_prompt}\n\nEvaluate the following place within its sub-event context and output ONLY a valid JSON object with flat keys. The 'PlaceName' key should be a string value, followed by top-level keys like 'GPS_Coordinate', 'BoundingBox' (as an object), 'External_Maps' (as an array), and 'ModernMap' (as an object). No nested 'PlaceName' object. No additional text or explanations:\n{json.dumps(place_data)}"

    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": place_prompt}],
        "max_tokens": 32768  # Adjustable; set to a value sufficient for one place
    }

    max_retries = 3
    backoff_time = 1  # Initial backoff in seconds

    for attempt in range(1, max_retries + 1):
        logger.info("Sending API request for place %s (attempt %d)", place_data['place'], attempt)
        try:
            start_time = time.time()
            response = requests.post(url, headers=headers, json=data, timeout=60)  # Add timeout to prevent hangs
            elapsed = time.time() - start_time
            logger.info("API request completed in %.4f seconds", elapsed)
            if response.status_code in range(500, 600):  # Server errors
                raise requests.RequestException(f"Server error: {response.status_code}")
            return response.json()["choices"][0]["message"]["content"]
        except requests.Timeout as e:
            logger.error("API request timed out (attempt %d): %s", attempt, e)
        except requests.RequestException as e:
            logger.error("API request failed (attempt %d): %s", attempt, e)
            time.sleep(backoff_time)
            backoff_time *= 2  # Exponential backoff
        except KeyError as e:
            logger.error("Unexpected response structure: %s", e)
            return None

    logger.error("Max retries exceeded for place %s", place_data['place'])
    return None


def process_places(base_prompt, json_file, chapter, section, logger):
    """Process each place with unique API requests and aggregate results, reusing cached responses where appropriate."""
    places = load_places(f"../{BOOK}/data/prompts/chapter{chapter}", f"chapter{chapter}{section}-review", f"chapter{chapter}{section}-event.json", logger)
    if not places:
        logger.warning("No places found in local source. Proceeding with empty output.")
        return

    results = {}
    for place_data in places:
        sub_index = place_data['sub_event_index']
        place = place_data['place']

        start_time = time.time()

        cached_response = cache_handler.get_cached_response(place, '')  # Empty summary for place-only caching
        if cached_response:
            elapsed = time.time() - start_time
            logger.info("Cache hit for place %s in sub-event %d, time: %.4f seconds", place, sub_index, elapsed)
            json_obj = cached_response
        else:
            response_content = send_api_request(base_prompt, place_data, logger)
            if response_content:
                try:
                    # Clean response (as before)
                    cleaned_content = response_content.strip()
                    if cleaned_content.startswith('```json'):
                        cleaned_content = cleaned_content[7:].rstrip('`')
                    elif cleaned_content.startswith('```'):
                        cleaned_content = cleaned_content[3:].rstrip('`')

                    json_obj = json.loads(cleaned_content)
                    cache_handler.cache_response(place, '', json_obj)  # Cache with empty summary for place-only
                except json.JSONDecodeError as e:
                    logger.warning("Invalid JSON response for place %s: %s. Response was: %s. Skipping.", place, e, response_content)
                    continue  # Skip invalid without caching
            else:
                continue  # Skip if no response

        if sub_index not in results:
            results[sub_index] = []
        results[sub_index].append(json_obj)

    # Write aggregated results as a JSON object (keyed by sub-event index)
    try:
        with open(json_file, 'w') as f:
            json.dump(results, f, indent=4)
    except IOError as e:
        logger.error("Unable to write to output file: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grok API submission script")
    parser.add_argument('--log-dir', type=str, help="Optional directory for log file output")
    args = parser.parse_args()

    logger = setup_logging(args.log_dir)

    print_environment_info(logger)

    # Get all (CHAPTER, SECTION) pairs from directory_scanner
    folders = directory_scanner.get_all_review_folders()
    if not folders:
        logger.warning("No review folders found. Exiting.")
        sys.exit(0)

    for chapter, section in folders:
        logger.info("\nProcessing CHAPTER = %s, SECTION = '%s'", chapter, section)

        # Compute dependent variables dynamically
        localsource = f"chapter{chapter}{section}-event.json"
        chapter_folder = f"../{BOOK}/data/prompts/chapter{chapter}"
        review_folder = f"chapter{chapter}{section}-review"
        target_file = f"00-chapter{chapter}{section}-{TYPE}-review.yaml"
        sourcelink = f"https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/USA-E-Breakout-{chapter}.html"
        footnotelink = f"https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn{chapter}.html"
        output_file = f"chapter{chapter}{section}-{TYPE}.json"

        create_review_directory(chapter_folder, review_folder, logger)
        copy_content_file(chapter, section, BOOK, chapter_folder, review_folder, logger)
        json_file = initialize_output_json(chapter_folder, review_folder, output_file, logger)
        target_path = assemble_target_yaml(BOOK, TYPE, chapter_folder, review_folder, target_file, logger)
        replace_placeholders(target_path, BOOKNAME, chapter, section, localsource, logger)
        clean_up_description_file(chapter_folder, review_folder, TYPE, logger)
        base_prompt = load_base_prompt(target_path, logger)
        process_places(base_prompt, json_file, chapter, section, logger)