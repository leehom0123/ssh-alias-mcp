#!/bin/bash
# Run the live e2e pytest suite inside WSL against the given server config.
# Usage: ./run-wsl-test.sh [SERVER_NAME]   (default: test-cmd)
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="$(dirname "$SCRIPT_DIR")"
ROOT_DIR="$(dirname "$TEST_DIR")"
source ~/miniconda3/etc/profile.d/conda.sh
conda activate ts
export SSH_TEST_SERVER="${1:-test-cmd}"
cd "$ROOT_DIR"
python -m pytest test/test_live_e2e.py -m live -q
