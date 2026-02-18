#!/bin/bash
# Load shell profile so ANTHROPIC_API_KEY is available
source ~/.zshrc

# Activate the conda environment
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate cat-automation

# Run the tool
cd "/Users/kirstenwittich/Documents/CAT Automation"
python tools/check_processor/main.py
