#!/usr/bin/env sh
set -eu
manifest="$1"
base=$(CDPATH= cd -- "$(dirname -- "$manifest")" && pwd)
archive=$(sed -n 's/^[[:space:]]*"archive": "\([^"]*\)",*$/\1/p' "$manifest")
expected=$(sed -n 's/^[[:space:]]*"sha256": "\([0-9a-f]*\)",*$/\1/p' "$manifest" | head -n 1)
parts=$(sed -n '/^[[:space:]]*"parts": \[/,/^[[:space:]]*\]/{s/^[[:space:]]*"\([^"]*\)",*$/\1/p;}' "$manifest")
test -n "$archive" && test -n "$expected" && test -n "$parts"
output="$base/$archive"
: > "$output"
for part in $parts; do
  cat "$base/$part" >> "$output"
done
actual=$(shasum -a 256 "$output" | awk '{print $1}')
test "$actual" = "$expected" || { echo "合并后的 SHA256 不匹配" >&2; exit 1; }
echo "已还原：$output"
