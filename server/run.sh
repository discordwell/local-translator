#!/bin/bash
#
# Local Translator Server - Launch Script
#
# This script sets up the environment and runs the translation server.
# Optimized for M4 Max MacBook with MPS (Metal) acceleration.
#
# Usage:
#   ./run.sh              # Run with WiFi (Bonjour) - requires network setup
#   ./run.sh --bluetooth  # Run with Bluetooth only - no network needed!
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Configuration
export PORT="${PORT:-8000}"

# PyTorch MPS (Metal) optimizations for Apple Silicon
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Hugging Face cache (optional: set custom location)
# export HF_HOME="$HOME/.cache/huggingface"

# Transformers settings
export TRANSFORMERS_OFFLINE=0  # Set to 1 after first run for offline mode

echo "========================================"
echo "  Local Translator Server"
echo "  日本語翻訳サーバー"
echo "========================================"
echo ""

# Check for Bluetooth mode
if [[ "$1" == "--bluetooth" ]] || [[ "$1" == "-b" ]]; then
    echo "Mode: BLUETOOTH (no WiFi needed)"
    echo ""
    BLUETOOTH_MODE="--bluetooth"
else
    echo "Mode: WiFi (Bonjour discovery)"
    echo "Port: $PORT"
    echo ""
    echo "Tip: Use './run.sh --bluetooth' for Bluetooth mode"
    echo "     (no network configuration required)"
    echo ""
    BLUETOOTH_MODE=""
fi

echo "Device: MPS (Metal) if available"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "Virtual environment created."
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "Checking dependencies..."
pip install -q -r requirements.txt

echo ""
echo "Starting server..."
echo "The model will be downloaded on first run (~9GB)."
echo "This may take a while depending on your connection."
echo ""

# Run the server
python main.py $BLUETOOTH_MODE
