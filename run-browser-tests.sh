#!/bin/sh
# Run the browser tests in the official Playwright image, so no browsers need
# installing locally.  Extra arguments go to pytest, e.g.
#   ./run-browser-tests.sh --browser chromium --browser firefox --mine humanmine
# Environment passed through: MINE_TESTS_PROXY, MINE_TESTS_IGNORE_TLS,
# HTTP(S)_PROXY (for pip and the browsers), MINE_TESTS_HTTP_TIMEOUT.
set -eu
IMAGE=mcr.microsoft.com/playwright/python:v1.58.0-noble   # keep in step with requirements.txt
cd "$(dirname "$0")"
mkdir -p test-results
exec docker run --rm -v "$PWD:/work" -w /work \
  -e HTTPS_PROXY -e HTTP_PROXY -e NO_PROXY -e MINE_TESTS_PROXY -e MINE_TESTS_IGNORE_TLS -e MINE_TESTS_HTTP_TIMEOUT \
  "$IMAGE" bash -c 'pip install -q -r requirements.txt 2>&1 | grep -v -e "\[notice\]" -e WARNING || true
                    pytest tests/browser --screenshot only-on-failure --tracing retain-on-failure "$@"' -- "$@"
