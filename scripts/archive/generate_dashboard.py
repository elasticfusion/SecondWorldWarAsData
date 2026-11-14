#!/usr/bin/env python3
"""Generate validation dashboard for all schemas."""

# pylint: disable=line-too-long  # HTML/CSS templates have long lines

import sys
from datetime import datetime
from pathlib import Path

from src.utils.json_validator import validate_directory
from src.utils.schema_registry import get_registry


def generate_dashboard_html(results_by_schema):
    """Generate HTML dashboard."""
    total_files = sum(r["total"] for r in results_by_schema.values())
    total_valid = sum(r["valid"] for r in results_by_schema.values())
    total_invalid = sum(r["invalid"] for r in results_by_schema.values())
    overall_rate = (total_valid / total_files * 100) if total_files > 0 else 0

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Validation Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background: #f5f7fa; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ font-size: 32px; margin-bottom: 10px; }}
        .timestamp {{ opacity: 0.9; font-size: 14px; }}
        .overview {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; padding: 20px; }}
        .overview-card {{ background: white; padding: 25px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .overview-value {{ font-size: 48px; font-weight: bold; margin: 10px 0; }}
        .overview-label {{ color: #666; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }}
        .success {{ color: #10b981; }}
        .warning {{ color: #f59e0b; }}
        .error {{ color: #ef4444; }}
        .schemas {{ padding: 20px; }}
        .schema-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 20px; }}
        .schema-card {{ background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }}
        .schema-card:hover {{ transform: translateY(-4px); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }}
        .schema-header {{ padding: 20px; border-bottom: 1px solid #e5e7eb; }}
        .schema-name {{ font-size: 20px; font-weight: 600; color: #1f2937; margin-bottom: 5px; }}
        .schema-path {{ font-size: 12px; color: #6b7280; }}
        .schema-stats {{ padding: 20px; }}
        .stat-row {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #f3f4f6; }}
        .stat-row:last-child {{ border-bottom: none; }}
        .stat-label {{ color: #6b7280; font-size: 14px; }}
        .stat-value {{ font-weight: 600; font-size: 18px; }}
        .progress-bar {{ height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden; margin-top: 15px; }}
        .progress-fill {{ height: 100%; background: linear-gradient(90deg, #10b981, #059669); transition: width 0.3s; }}
        .badge {{ display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
        .badge-success {{ background: #d1fae5; color: #065f46; }}
        .badge-warning {{ background: #fef3c7; color: #92400e; }}
        .badge-error {{ background: #fee2e2; color: #991b1b; }}
        .no-data {{ text-align: center; padding: 60px 20px; color: #9ca3af; }}
        .refresh-btn {{ background: white; color: #667eea; border: 2px solid white; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
        .refresh-btn:hover {{ background: rgba(255,255,255,0.9); }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>📊 Validation Dashboard</h1>
            <div class="timestamp">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        </div>
    </div>
    
    <div class="overview">
        <div class="overview-card">
            <div class="overview-label">Total Files</div>
            <div class="overview-value">{total_files}</div>
        </div>
        <div class="overview-card">
            <div class="overview-label">Valid</div>
            <div class="overview-value success">{total_valid}</div>
        </div>
        <div class="overview-card">
            <div class="overview-label">Invalid</div>
            <div class="overview-value error">{total_invalid}</div>
        </div>
        <div class="overview-card">
            <div class="overview-label">Success Rate</div>
            <div class="overview-value {'success' if overall_rate >= 95 else 'warning' if overall_rate >= 80 else 'error'}">{overall_rate:.1f}%</div>
        </div>
    </div>
    
    <div class="schemas">
        <div class="container">
            <div class="schema-grid">
"""

    for schema_name, result in results_by_schema.items():
        if result["total"] == 0:
            continue

        success_rate = (
            (result["valid"] / result["total"] * 100) if result["total"] > 0 else 0
        )
        badge_class = (
            "badge-success"
            if success_rate >= 95
            else "badge-warning" if success_rate >= 80 else "badge-error"
        )
        badge_text = (
            "Excellent"
            if success_rate >= 95
            else "Good" if success_rate >= 80 else "Needs Attention"
        )

        html += f"""
                <div class="schema-card">
                    <div class="schema-header">
                        <div class="schema-name">{schema_name.title()}</div>
                        <div class="schema-path">{result['directory']}</div>
                    </div>
                    <div class="schema-stats">
                        <div class="stat-row">
                            <span class="stat-label">Total Files</span>
                            <span class="stat-value">{result['total']}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Valid</span>
                            <span class="stat-value success">{result['valid']}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Invalid</span>
                            <span class="stat-value error">{result['invalid']}</span>
                        </div>
                        <div class="stat-row">
                            <span class="stat-label">Status</span>
                            <span class="badge {badge_class}">{badge_text}</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {success_rate}%"></div>
                        </div>
                    </div>
                </div>
"""

    if not any(r["total"] > 0 for r in results_by_schema.values()):
        html += """
                <div class="no-data">
                    <h2>No data files found</h2>
                    <p>Add JSON files to output/ directories to see validation results</p>
                </div>
"""

    html += """
            </div>
        </div>
    </div>
</body>
</html>
"""
    return html


def main():
    """Generate validation dashboard."""
    registry = get_registry()
    base_dir = Path("output")

    results_by_schema = {}

    # Validate each schema
    for schema_name in registry.list_schemas():
        data_dir = base_dir / schema_name
        schema = registry.get_schema(schema_name)

        if data_dir.exists():
            print(f"Validating {schema_name}...")
            results = validate_directory(data_dir, schema)
            results["directory"] = str(data_dir)
            results_by_schema[schema_name] = results
        else:
            results_by_schema[schema_name] = {
                "total": 0,
                "valid": 0,
                "invalid": 0,
                "errors": [],
                "directory": str(data_dir),
            }

    # Generate dashboard
    html = generate_dashboard_html(results_by_schema)

    # Save
    output_path = Path("validation_dashboard.html")
    output_path.write_text(html, encoding="utf-8")

    print(f"\n✓ Dashboard generated: {output_path}")
    print(f"  Open in browser: file://{output_path.absolute()}")

    # Summary
    total_files = sum(r["total"] for r in results_by_schema.values())
    total_invalid = sum(r["invalid"] for r in results_by_schema.values())

    if total_invalid > 0:
        print(f"\n⚠ {total_invalid} invalid files found")
        return 1
    if total_files > 0:
        print(f"\n✅ All {total_files} files valid")
        return 0
    else:
        print("\nℹ No data files found")
        return 0


if __name__ == "__main__":
    sys.exit(main())
