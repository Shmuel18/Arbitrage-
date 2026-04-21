#!/bin/bash
# RateBridge log tail with color coding by level
journalctl -u ratebridge -f --no-pager -o cat | while IFS= read -r line; do
  if [[ "$line" == *'"level": "ERROR"'* ]]; then
    printf "\033[31m%s\033[0m\n" "$line"
  elif [[ "$line" == *'"level": "WARNING"'* ]]; then
    printf "\033[33m%s\033[0m\n" "$line"
  elif [[ "$line" == *'"level": "INFO"'* ]]; then
    printf "\033[32m%s\033[0m\n" "$line"
  else
    echo "$line"
  fi
done
