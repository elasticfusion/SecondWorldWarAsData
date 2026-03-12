"""Example: Using custom validators with validation hooks."""

from pathlib import Path

from src.utils.custom_validators import validate_data_with_custom_validators
from src.utils.json_validator import register_pre_validation_hook


def custom_validation_hook(data):
    """Pre-validation hook that runs custom validators."""
    results = validate_data_with_custom_validators(data, Path("output"))

    # Log errors
    if results["errors"]:
        print(f"Custom validation errors: {len(results['errors'])}")
        for error in results["errors"]:
            print(f"  - {error}")

    # Log warnings
    if results["warnings"]:
        print(f"Custom validation warnings: {len(results['warnings'])}")
        for warning in results["warnings"]:
            print(f"  - {warning}")


# Register the hook
register_pre_validation_hook(custom_validation_hook)

print("✓ Custom validators registered as pre-validation hook")
print("  - ULID format validation")
print("  - ISO date validation")
print("  - URL validation")
print("  - Cross-reference validation")
