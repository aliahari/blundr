#!/bin/bash

# Setup development environment using uv

echo "Setting up Blundr development environment..."
echo "============================================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "UV is not installed. Installing uv..."
    curl -Ls https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "UV version: $(uv --version)"
echo ""

# Create virtual environment with uv
echo "Creating virtual environment..."
uv venv

echo "Virtual environment created at .venv"
echo ""

# Install dependencies
echo "Installing dependencies..."
uv sync

echo ""
echo "============================================"
echo "Development environment setup complete!"
echo ""
echo "To activate the virtual environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To run the backend:"
echo "  uv run python run.py"
echo ""
echo "To run tests:"
echo "  uv run pytest"
echo ""
