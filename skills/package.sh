#!/usr/bin/env bash
# Package a skill folder as a .zip for upload to Claude Desktop / API.
# The zip contains the skill folder at its root, per Anthropic's spec.
#
# Usage: ./package.sh <skill-name> [<skill-name> ...]
#        ./package.sh --all
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p dist

pack() {
  local name=$1
  if [[ ! -d "$name" ]]; then
    echo "skip: $name (not a directory)" >&2
    return
  fi
  if [[ ! -f "$name/SKILL.md" ]]; then
    echo "skip: $name (no SKILL.md)" >&2
    return
  fi
  rm -f "dist/$name.zip"
  zip -qr "dist/$name.zip" "$name" -x "$name/__pycache__/*" "$name/*/__pycache__/*"
  echo "dist/$name.zip"
}

if [[ ${1:-} == "--all" ]]; then
  for d in */; do
    [[ -f "$d/SKILL.md" ]] && pack "${d%/}"
  done
elif [[ $# -gt 0 ]]; then
  for name in "$@"; do pack "$name"; done
else
  echo "usage: $0 <skill-name> [...] | --all" >&2
  exit 2
fi
