#!/bin/zsh
set -eu

cd -- "${0:A:h}"

if command -v uv >/dev/null 2>&1; then
  uv sync --frozen --no-dev --inexact
else
  if [[ ! -x .venv/bin/python ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
      print -u2 "Для запуска нужен Python 3.11 или новее."
      exit 1
    fi
    python3 -m venv .venv
  fi
  if ! .venv/bin/python -c 'import certifi, fastapi, keyring, uvicorn' >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
  fi
fi

if [[ ! -x "Second Brain.app/Contents/MacOS/SecondBrain" ]]; then
  ./scripts/build-app.sh
fi

open "Second Brain.app"
