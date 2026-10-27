"""HTML uploads route to the convert step (pandoc html->md), not straight to parse."""

import os


def test_html_routes_to_convert_not_parse():
    # Minimal env for trigger_handler's module-level boto3 client; imported lazily INSIDE the
    # test so this file adds no module-scope import side effects to the shared suite.
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("S3_BUCKET", "test-bucket")
    os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")

    import lambda_handlers.trigger_handler as T

    keys = [
        "contentrepository/RoerDams/page.html",
        "contentrepository/RoerDams/p2.htm",
        "contentrepository/RoerDams/p3.xhtml",
        "contentrepository/RoerDams/chapter.md",
        "contentrepository/RoerDams/book.epub",
    ]
    _ocr, convert, _video, parse = T._split_by_media(keys)
    assert "contentrepository/RoerDams/page.html" in convert
    assert "contentrepository/RoerDams/p2.htm" in convert
    assert "contentrepository/RoerDams/p3.xhtml" in convert
    assert "contentrepository/RoerDams/book.epub" in convert
    # .md still parses directly; no .html leaks into parse
    assert "contentrepository/RoerDams/chapter.md" in parse
    assert not any(k.endswith((".html", ".htm", ".xhtml")) for k in parse)
