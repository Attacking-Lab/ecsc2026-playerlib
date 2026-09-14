#!/usr/bin/env bash

# openapi.yaml is a verbatim copy of the gameserver's own scoreboard schema
# (gameserver:scoreboard/schema/openapi.yaml); tests/test_schema.py fails if the two drift.
# These are the gameserver's own generation options, at the py310 preset rather than its
# py312 one, since this package still supports Python 3.10. The generator and the ruff it
# formats with are both pinned by uv.lock (dev group), so regenerating reproduces the
# committed file byte for byte.

set -e

cd "$(dirname "$0")"

PARAMS=" \
  --input-file-type openapi --output-model-type pydantic_v2.BaseModel --preset standard-py310-20260619 \
  --formatters ruff-check ruff-format \
  --use-default --use-default-kwarg --use-generic-base-class --disable-future-imports --use-title-as-name --use-double-quotes --use-schema-description \
  --aliases aliases.json"
OUTPUT_DIR="${1:-./src/ecsc2026ad}"

uv run --no-sync datamodel-codegen --input "openapi.yaml" $PARAMS --output "$OUTPUT_DIR"/api_models.py

echo "DONE"
