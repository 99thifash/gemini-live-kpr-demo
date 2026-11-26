#!/bin/zsh
source ../../.venv/bin/activate
if [ -z "$GEMINI_API_KEY" ]; then
  echo -n "Enter your Gemini API key: "
  read -s GEMINI_API_KEY
  echo
  export GEMINI_API_KEY
fi
python -u main.py | tee "log_${1:-skenario}.txt"
