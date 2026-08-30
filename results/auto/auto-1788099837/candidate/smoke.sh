#!/bin/bash
set -e
./venv/bin/python train.py --claim dt_fashion_1 --seed 42 --set data.dir=localdata > /tmp/smoke_out.json
echo "Smoke test completed. Output:"
tail -1 /tmp/smoke_out.json
