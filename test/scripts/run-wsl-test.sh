#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate ts
export SSH_TEST_SERVER="test-cmd"
python /mnt/d/repos/2026/ssh-alias-mcp/test/test_cmd.py
