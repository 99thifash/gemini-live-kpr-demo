#!/bin/zsh
cd "$(dirname "$0")"
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  source ../../.venv/bin/activate
fi
if [ -z "$GEMINI_API_KEY" ]; then
  echo -n "Enter your Gemini API key: "
  read -s GEMINI_API_KEY
  echo
  export GEMINI_API_KEY
fi
python -u main.py | tee "log_${1:-skenario}.txt"
