#!/bin/sh
# Run against a local Next dev/start server and backend; no credentials needed.
# A healthy proxy forwards the complete large request and the API returns 401.
set -eu
base_url=${1:-http://127.0.0.1:3000}
fixture=$(mktemp --suffix=.docx)
response=$(mktemp)
trap 'rm -f "$fixture" "$response"' EXIT HUP INT TERM
truncate -s 28M "$fixture"
status=$(curl -sS --max-time 15 -o "$response" -w '%{http_code}' \
  -F 'knowledge_base_id=default' -F "file=@$fixture" \
  "$base_url/rag-api/api/v1/admin/document-imports")
if [ "$status" != 401 ]; then
  printf 'Large upload proxy regression: expected 401 (unauthenticated), got %s\n' "$status" >&2
  exit 1
fi
printf 'Large upload proxy forwarded 28 MB successfully (backend returned 401).\n'
