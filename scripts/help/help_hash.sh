#!/bin/bash
# sha256 of a help folder (default content/help): every JSON file except
# manifest.json and articles/index.json, by relative path. The app's bundled
# copy records it in assets/help/manifest.json ("hash").
#   scripts/help/help_hash.sh [folder]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${1:-$REPO/content/help}"
find . -name '*.json' ! -name manifest.json ! -path './articles/index.json' | LC_ALL=C sort | while read -r f; do
  printf '%s\n' "$f"
  cat "$f"
done | shasum -a 256 | cut -d' ' -f1
