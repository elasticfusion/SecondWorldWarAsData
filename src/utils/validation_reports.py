"""Validation reporting utilities."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def generate_validation_report(
    results: Dict[str, Any],
    schema_name: str,
    directory: Path,
    output_format: str = "json",
) -> str:
    """
    Generate validation report.

    Args:
        results: Validation results from validate_directory()
        schema_name: Schema name
        directory: Directory that was validated
        output_format: 'json' or 'html'

    Returns:
        Report content as string
    """
    report_data = {
        "timestamp": datetime.now().isoformat(),
        "schema": schema_name,
        "directory": str(directory),
        "summary": {
            "total": results["total"],
            "valid": results["valid"],
            "invalid": results["invalid"],
            "success_rate": (
                f"{results['valid'] / results['total'] * 100:.1f}%"
                if results["total"] > 0
                else "0%"
            ),
        },
        "errors": results["errors"],
        "custom_validation": results.get("custom_validation", {"enabled": False}),
    }

    if output_format == "html":
        return _generate_html_report(report_data)
    return json.dumps(report_data, indent=2)


def _generate_html_report(data: Dict[str, Any]) -> str:
    """Generate HTML validation report."""
    summary = data["summary"]
    errors = data["errors"]

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Validation Report - {data['schema']}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
        .metric {{ background: #f9f9f9; padding: 15px; border-radius: 4px; border-left: 4px solid #4CAF50; }}
        .metric.error {{ border-left-color: #f44336; }}
        .metric-value {{ font-size: 32px; font-weight: bold; color: #333; }}
        .metric-label {{ color: #666; font-size: 14px; margin-top: 5px; }}
        .errors {{ margin-top: 30px; }}
        .error-item {{ background: #fff3f3; border-left: 4px solid #f44336; padding: 15px; margin: 10px 0; border-radius: 4px; }}
        .error-file {{ font-weight: bold; color: #d32f2f; }}
        .error-message {{ color: #666; margin-top: 5px; font-family: monospace; font-size: 12px; }}
        .timestamp {{ color: #999; font-size: 12px; }}
        .success {{ color: #4CAF50; }}
        .failure {{ color: #f44336; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Validation Report</h1>
        <div class="timestamp">Generated: {data['timestamp']}</div>
        <p><strong>Schema:</strong> {data['schema']}</p>
        <p><strong>Directory:</strong> {data['directory']}</p>
        
        <div class="summary">
            <div class="metric">
                <div class="metric-value">{summary['total']}</div>
                <div class="metric-label">Total Files</div>
            </div>
            <div class="metric">
                <div class="metric-value success">{summary['valid']}</div>
                <div class="metric-label">Valid</div>
            </div>
            <div class="metric error">
                <div class="metric-value failure">{summary['invalid']}</div>
                <div class="metric-label">Invalid</div>
            </div>
            <div class="metric">
                <div class="metric-value">{summary['success_rate']}</div>
                <div class="metric-label">Success Rate</div>
            </div>
        </div>
"""

    if errors:
        html += f"""
        <div class="errors">
            <h2>Errors ({len(errors)})</h2>
"""
        for error in errors:
            html += f"""
            <div class="error-item">
                <div class="error-file">{error['file']}</div>
                <div class="error-message">{error['error']}</div>
            </div>
"""
        html += """
        </div>
"""
    else:
        html += """
        <div style="background: #e8f5e9; padding: 20px; border-radius: 4px; text-align: center; margin-top: 20px;">
            <h2 style="color: #4CAF50; margin: 0;">✓ All files valid!</h2>
        </div>
"""

    html += """
    </div>
</body>
</html>
"""
    return html


def save_validation_history(
    results: Dict[str, Any], schema_name: str, directory: Path, history_file: Path
) -> None:
    """
    Append validation results to history file.

    Args:
        results: Validation results
        schema_name: Schema name
        directory: Directory validated
        history_file: Path to history JSON file
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "schema": schema_name,
        "directory": str(directory),
        "total": results["total"],
        "valid": results["valid"],
        "invalid": results["invalid"],
        "error_count": len(results["errors"]),
    }

    # Load existing history
    history: List[Dict[str, Any]] = []
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text())
        except json.JSONDecodeError:
            history = []

    # Append new entry
    history.append(entry)

    # Keep last 100 entries
    history = history[-100:]

    # Save
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(history, indent=2))


def generate_trend_report(history_file: Path, schema_name: str) -> str:
    """
    Generate trend report from validation history.

    Args:
        history_file: Path to history JSON file
        schema_name: Schema to filter by (or 'all')

    Returns:
        HTML report showing trends
    """
    if not history_file.exists():
        return "<html><body><h1>No validation history found</h1></body></html>"

    history = json.loads(history_file.read_text())

    # Filter by schema if specified
    if schema_name != "all":
        history = [h for h in history if h.get("schema") == schema_name]

    if not history:
        return (
            f"<html><body><h1>No history for schema: {schema_name}</h1></body></html>"
        )

    # Calculate trends
    total_runs = len(history)
    avg_success_rate = (
        sum(h["valid"] / h["total"] * 100 if h["total"] > 0 else 0 for h in history)
        / total_runs
    )
    recent = history[-10:]

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Validation Trends - {schema_name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }}
        h1 {{ color: #333; border-bottom: 2px solid #2196F3; padding-bottom: 10px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
        .metric {{ background: #f9f9f9; padding: 15px; border-radius: 4px; border-left: 4px solid #2196F3; }}
        .metric-value {{ font-size: 32px; font-weight: bold; color: #333; }}
        .metric-label {{ color: #666; font-size: 14px; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #2196F3; color: white; padding: 12px; text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
        tr:hover {{ background: #f5f5f5; }}
        .success {{ color: #4CAF50; }}
        .failure {{ color: #f44336; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Validation Trends</h1>
        <p><strong>Schema:</strong> {schema_name}</p>
        
        <div class="summary">
            <div class="metric">
                <div class="metric-value">{total_runs}</div>
                <div class="metric-label">Total Runs</div>
            </div>
            <div class="metric">
                <div class="metric-value">{avg_success_rate:.1f}%</div>
                <div class="metric-label">Avg Success Rate</div>
            </div>
        </div>
        
        <h2>Recent Validations (Last 10)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Directory</th>
                    <th>Total</th>
                    <th>Valid</th>
                    <th>Invalid</th>
                    <th>Success Rate</th>
                </tr>
            </thead>
            <tbody>
"""

    for entry in reversed(recent):
        success_rate = (
            entry["valid"] / entry["total"] * 100 if entry["total"] > 0 else 0
        )
        rate_class = "success" if success_rate >= 95 else "failure"
        html += f"""
                <tr>
                    <td>{entry['timestamp'][:19]}</td>
                    <td>{entry['directory']}</td>
                    <td>{entry['total']}</td>
                    <td class="success">{entry['valid']}</td>
                    <td class="failure">{entry['invalid']}</td>
                    <td class="{rate_class}">{success_rate:.1f}%</td>
                </tr>
"""

    html += """
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html
