"""Parse Chandra OCR+AI markdown tables into structured JSON (ETO OOB, Piece 2).

Structure for scanned documents is recovered from the OCR+AI (Chandra) markdown,
not from PDF geometry (see docs/current/dataquality/INGESTION_FRONT_END.md,
"Scanned documents"). This package parses those markdown tables.

Increment 1 covers the COMMAND AND STAFF section. Later increments add the other
OOB sections (statistics, campaigns, organic units, attachments, detachments,
higher-unit assignments, command posts) using the same framework.
"""
