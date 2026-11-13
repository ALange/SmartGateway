#!/usr/bin/env bash
# Usage: tools/load.sh [base_url] [seconds]   -- generates mixed traffic against gateway "main"
B=${1:-http://localhost:8080}; D=${2:-30}; end=$((SECONDS+D))
chat() { curl -s -o /dev/null "$B/gw/main/v1/chat/completions" -H 'content-type: application/json' -d "$1"; }
while [ $SECONDS -lt $end ]; do
  for i in 1 2 3 4; do chat '{"model":"smart-1","stream":true,"max_tokens":12,"messages":[{"role":"user","content":"hi"}]}' & done
  chat '{"model":"smart-1","max_tokens":6,"reasoning_effort":"high","messages":[{"role":"user","content":"think"}]}' &
  chat '{"model":"smart-1","max_tokens":4,"messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,xx"}}]}]}' &
  curl -s -o /dev/null "$B/gw/main/v1/embeddings" -H 'content-type: application/json' -d '{"model":"embed","input":"x"}' &
  sleep 0.8
done; wait
