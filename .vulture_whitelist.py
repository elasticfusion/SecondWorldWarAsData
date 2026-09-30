# Vulture whitelist for false positives

# Pydantic validators require 'cls' parameter (classmethod)
_.cls  # src/extraction/equipment.py

# API consistency - parameter kept for interface compatibility
_.parsed_dir  # src/extraction/maps.py

# AWS Lambda / Step Functions entrypoints — invoked by AWS, not by our code.
# vulture can't see the (external) caller, so whitelist to avoid false positives.
handler  # lambda_handlers/*.py (Lambda entrypoints)
enumerate_pending  # lambda_handlers/dispatcher_handlers.py (SFN task)
clamp_pool  # lambda_handlers/dispatcher_handlers.py (SFN task)
human_gate  # lambda_handlers/dispatcher_handlers.py (SFN task)

# Protocol method params (bodies are '...' — vulture can't see they're the
# interface signature implemented elsewhere). src/ingestion/video_perception.py
_.mid_seconds  # FrameSampler.sample interface param
_.transcript_context  # VisionAnalyzer.analyze_frame interface param
