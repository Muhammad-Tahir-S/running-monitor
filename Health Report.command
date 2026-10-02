#!/bin/bash
# Double-click to start the report service and open the report.
cd "$(dirname "$0")" || exit 1
PORT=$(python3 -c 'import json; print(json.load(open("config.json"))["server"]["port"])')
URL="http://127.0.0.1:$PORT/"

if ! curl -s -o /dev/null "${URL}status"; then
  nohup python3 sync_server.py >> server.log 2>&1 &
  for _ in $(seq 1 40); do
    curl -s -o /dev/null "${URL}status" && break
    sleep 0.5
  done
fi

open "$URL"
