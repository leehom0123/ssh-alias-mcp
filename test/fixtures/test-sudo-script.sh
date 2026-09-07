#!/bin/bash
# fixture: test-sudo-script (runs as root via alias sudo flag)
id -u | grep -q '^0$' || { echo "not-root"; exit 9; }
echo "test-hello-script-ok"
