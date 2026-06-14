#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate ts
echo "=== Python path ==="
which python
echo "=== Python version ==="
python --version
echo "=== Paramiko ==="
python -c "import paramiko; print(paramiko.__version__)"
echo "=== PyYAML ==="
python -c "import yaml; print(yaml.__version__)"
