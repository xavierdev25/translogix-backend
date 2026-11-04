#!/bin/sh
# Generates the frontend types from openapi.yaml and records which YAML they came from.
# Version pinned so every agent produces byte-identical output.
set -eu
cd "$(dirname "$0")"
npx --yes openapi-typescript@7.13.0 ../openapi.yaml -o api.d.ts
shasum -a 256 ../openapi.yaml | cut -d ' ' -f 1 > openapi.sha256
echo "types/api.d.ts generated from openapi.yaml ($(cat openapi.sha256))"
