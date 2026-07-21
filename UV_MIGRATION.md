# UV Migration Guide

This document explains how to migrate from pip/requirements.txt to uv for Python package management.

## What is UV?

[UV](https://github.com/astral-sh/uv) is a modern Python package manager that:
- Is **blazingly fast** (written in Rust)
- Handles virtual environments automatically
- Has a single lockfile format
- Supports PyPI and local packages
- Is fully compatible with pip and requirements.txt

## Why Migrate?

1. **Speed**: UV is significantly faster than pip
2. **Simplicity**: Single command to create venv + install dependencies
3. **Reliability**: Deterministic dependency resolution
4. **Modern**: Built for the modern Python ecosystem

## Changes Made to This Project

### Added Files
- `pyproject.toml` - Project configuration and dependencies
- `.python-version` - Python version specification
- `.uvignore` - Files for UV to ignore
- `Makefile` - Common tasks with UV commands
- `scripts/setup_dev.sh` - Setup script using UV

### Modified Files
- `Dockerfile` - Now uses UV to install dependencies
- `run.py` - Updated comments to mention UV
- `run_dev.sh` - Uses UV to run the backend
- `.gitignore` - Added `.venv/` to ignore UV virtual environments
- `README.md` - Updated documentation to use UV

### Kept for Backward Compatibility
- `requirements.txt` - Still present for users who prefer pip

## Migration Steps

### For New Users

Simply follow the updated README instructions:

```bash
# Install UV
curl -Ls https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv sync

# Run the backend
uv run python run.py
```

### For Existing Users

If you already have a pip-based environment:

#### Option 1: Fresh Start (Recommended)

```bash
# Remove old virtual environment
rm -rf venv

# Install UV
curl -Ls https://astral.sh/uv/install.sh | sh

# Create new virtual environment with UV
uv venv

# Install dependencies
uv sync

# Test
uv run pytest
uv run python run.py
```

#### Option 2: Gradual Migration

Keep using your existing pip environment and gradually adopt UV:

```bash
# Install UV
curl -Ls https://astral.sh/uv/install.sh | sh

# Run commands with UV (will use system Python)
uv run python run.py
uv run pytest
```

## UV Commands Cheat Sheet

| pip command | uv equivalent |
|-------------|----------------|
| `pip install -r requirements.txt` | `uv sync` (uses pyproject.toml) |
| `pip install <package>` | `uv add <package>` |
| `pip uninstall <package>` | `uv remove <package>` |
| `python -m pip list` | `uv pip list` |
| `python -m venv venv` | `uv venv` |
| `source venv/bin/activate` | Not needed (use `uv run`) |
| `python script.py` | `uv run python script.py` |
| `pytest` | `uv run pytest` |

## Common UV Commands

### Project Management
```bash
# Create virtual environment
uv venv

# Install all dependencies from pyproject.toml
uv sync

# Install a new package and add to pyproject.toml
uv add requests

# Remove a package
uv remove requests

# Show installed packages
uv pip list

# Update all packages
uv lock --upgrade
uv sync
```

### Running Commands
```bash
# Run Python in the virtual environment
uv run python

# Run a script
uv run python run.py

# Run tests
uv run pytest

# Run with specific Python version
uv run --python 3.11 python run.py
```

### Virtual Environment
```bash
# Create venv in .venv directory
uv venv

# Use a specific Python version
uv venv --python 3.11

# List all virtual environments
uv venv --list

# Remove a virtual environment
rm -rf .venv
```

## Project Configuration

The project now uses `pyproject.toml` instead of `requirements.txt`. Example:

```toml
[project]
name = "blundr"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "httpx>=0.26.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "ruff>=0.1.0",
]
```

## Lockfile

UV automatically creates a `uv.lock` file that:
- Locks all dependency versions
- Ensures reproducible builds
- Can be committed to version control

To update dependencies:
```bash
uv lock --upgrade
uv sync
```

## Troubleshooting

### UV not found
```bash
# Install UV
curl -Ls https://astral.sh/uv/install.sh | sh

# Add to PATH (if not automatic)
export PATH="$HOME/.local/bin:$PATH"
```

### Can I still use pip?
Yes! UV is fully compatible with pip. You can:
- Keep using pip in your existing environment
- Use `uv pip install` for pip-like commands
- UV will use the same Python interpreter as your system

### How do I know UV is using the virtual environment?
```bash
# Check Python path
uv run python -c "import sys; print(sys.executable)"

# Should show path to .venv/bin/python
```

## Backward Compatibility

The project still includes `requirements.txt` for users who prefer pip. You can:

1. Use UV (recommended):
   ```bash
   uv sync
   uv run python run.py
   ```

2. Use pip (legacy):
   ```bash
   pip install -r requirements.txt
   python run.py
   ```

Both methods work and install the same dependencies.

## Benefits of UV

### Speed Comparison
```bash
# UV (typically 1-2 seconds)
time uv add requests

# pip (typically 10-30 seconds)
time pip install requests
```

### Disk Usage
UV virtual environments are smaller than pip's because UV shares packages between environments when possible.

### Deterministic Builds
UV lockfile ensures everyone gets the exact same dependency versions.

## Resources

- [UV Documentation](https://docs.astral.sh/uv/)
- [UV GitHub](https://github.com/astral-sh/uv)
- [Installation Guide](https://docs.astral.sh/uv/getting-started/installation)
- [Migration from pip](https://docs.astral.sh/uv/getting-started/migrating-from-pip/)
