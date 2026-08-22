#!/usr/bin/env bash
set -euo pipefail

compose_file="${1:-docker-compose.yml}"
example_file="${2:-.env.example}"
test_env_file="${3:-.github/test.env}"

if [[ ! -f "$compose_file" || ! -f "$example_file" || ! -f "$test_env_file" ]]; then
  echo "Required Compose validation input is missing" >&2
  printf 'compose=%s example=%s test_env=%s\n' "$compose_file" "$example_file" "$test_env_file" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

grep -oE '\$\{[A-Z_][A-Z0-9_]*|\$[A-Z_][A-Z0-9_]*' "$compose_file" |
  sed -E 's/^\$\{//; s/^\$//' |
  sort -u > "$tmp_dir/referenced"
grep -oE '^[A-Z_][A-Z0-9_]*=' "$example_file" |
  cut -d= -f1 |
  sort -u > "$tmp_dir/documented"
grep -oE '^[A-Z_][A-Z0-9_]*=' "$test_env_file" |
  cut -d= -f1 |
  sort -u > "$tmp_dir/test-env"

undocumented="$(comm -23 "$tmp_dir/referenced" "$tmp_dir/documented")"
missing="$(comm -23 "$tmp_dir/referenced" "$tmp_dir/test-env")"

if [[ -n "$undocumented" || -n "$missing" ]]; then
  if [[ -n "$undocumented" ]]; then
    echo "Referenced variables missing from $example_file:" >&2
    printf '%s\n' "$undocumented" >&2
  fi
  if [[ -n "$missing" ]]; then
    echo "Referenced variables missing from $test_env_file:" >&2
    printf '%s\n' "$missing" >&2
  fi
  exit 1
fi

echo "All referenced Compose variables are documented and present in $test_env_file."
