"""URL validation for supplemental material."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def validate_url(url: str, timeout: float = 10.0) -> tuple[str, Optional[str]]:
    """
    Validate a single URL.

    Returns:
        tuple: (status, error_message)
        status: "validated", "broken", "timeout", "invalid"
    """
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 200:
            return ("validated", None)
        return ("broken", f"HTTP {response.status_code}")
    except requests.Timeout:
        return ("timeout", "Request timed out")
    except requests.RequestException as e:
        return ("invalid", str(e))
    except Exception as e:
        return ("invalid", str(e))


def validate_material_urls(material: Dict[str, Any]) -> None:
    """
    Validate URLs in a supplemental material entry (modifies in place).

    Updates:
        - url_validation_status: Overall status for all URLs
        - url_validation_date: Current date
    """
    urls = material.get("resource_urls", [])

    if not urls:
        material["url_validation_status"] = "no_urls"
        material["url_validation_date"] = datetime.now().strftime("%Y-%m-%d")
        return

    statuses = []
    for url in urls:
        logger.debug("Validating URL: %s", url)
        status, error = validate_url(url)
        statuses.append(status)

        if error:
            logger.warning("URL validation failed for %s: %s", url, error)

    # Determine overall status
    if all(s == "validated" for s in statuses):
        overall_status = "validated"
    elif any(s == "validated" for s in statuses):
        overall_status = "partial"
    elif all(s == "timeout" for s in statuses):
        overall_status = "timeout"
    else:
        overall_status = "broken"

    material["url_validation_status"] = overall_status
    material["url_validation_date"] = datetime.now().strftime("%Y-%m-%d")

    logger.info("Validated %d URL(s): %s", len(urls), overall_status)


def validate_supplemental_file(file_path: Path, save: bool = True) -> Dict[str, int]:
    """
    Validate all URLs in a supplemental material file.

    Args:
        file_path: Path to endnotes.json or footnotes.json
        save: Whether to save changes back to file

    Returns:
        dict: Statistics (validated, broken, timeout, no_urls)
    """
    import json

    logger.info("Validating URLs in %s", file_path.name)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Failed to load %s: %s", file_path, e)
        return {}

    stats = {"validated": 0, "partial": 0, "broken": 0, "timeout": 0, "no_urls": 0}

    for entry in data:
        for material in entry.get("Supplemental_Material", []):
            validate_material_urls(material)
            status = material.get("url_validation_status", "unknown")
            stats[status] = stats.get(status, 0) + 1

    if save:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Saved validation results to %s", file_path.name)
        except (OSError, IOError) as e:
            logger.error("Failed to save %s: %s", file_path, e)

    return stats


def validate_all_supplemental(output_root: Path, save: bool = True) -> None:
    """
    Validate URLs in all supplemental material files.

    Args:
        output_root: Root output directory
        save: Whether to save changes back to files
    """
    total_stats = {
        "validated": 0,
        "partial": 0,
        "broken": 0,
        "timeout": 0,
        "no_urls": 0,
    }
    files_processed = 0

    # Find all endnotes and footnotes files
    for file_path in output_root.rglob("*-endnotes.json"):
        stats = validate_supplemental_file(file_path, save)
        for key, value in stats.items():
            total_stats[key] = total_stats.get(key, 0) + value
        files_processed += 1

    for file_path in output_root.rglob("*-footnotes.json"):
        stats = validate_supplemental_file(file_path, save)
        for key, value in stats.items():
            total_stats[key] = total_stats.get(key, 0) + value
        files_processed += 1

    logger.info("=" * 60)
    logger.info("URL Validation Summary")
    logger.info("=" * 60)
    logger.info("Files processed: %d", files_processed)
    logger.info("Validated: %d", total_stats.get("validated", 0))
    logger.info("Partial: %d", total_stats.get("partial", 0))
    logger.info("Broken: %d", total_stats.get("broken", 0))
    logger.info("Timeout: %d", total_stats.get("timeout", 0))
    logger.info("No URLs: %d", total_stats.get("no_urls", 0))
    logger.info("=" * 60)
