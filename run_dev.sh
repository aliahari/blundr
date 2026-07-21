#!/bin/bash

# Run both backend and frontend in development mode
# Uses uv for Python package management

echo "Starting Blundr development servers..."
echo "======================================"
echo ""

# Stop any previous instances first so restarts are one command
if [ -x "$(dirname "$0")/scripts/stop_dev.sh" ]; then
    "$(dirname "$0")/scripts/stop_dev.sh"
    echo ""
fi

# Check if backend dependencies are installed
if [ ! -d "app" ]; then
    echo "Error: Backend not found. Run from project root."
    exit 1
fi

# Check if .venv exists, if not try to create it with uv
if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Trying to create with uv..."
    if command -v uv &> /dev/null; then
        uv venv
        uv sync
    else
        echo "uv not found. Trying with pip..."
        python -m venv .venv
        source .venv/bin/activate
        pip install -r requirements.txt
        deactivate
    fi
fi

# Start backend in background using uv
echo "Starting backend server on http://localhost:8000..."
uv run python run.py &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 5

# Check if frontend exists
if [ -d "frontend" ]; then
    cd frontend
    
    # Install frontend dependencies if needed
    if [ ! -d "node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm install
    fi
    
    echo "Starting frontend server on http://localhost:3000..."
    echo ""
    echo "======================================"
    echo "Backend: http://localhost:8000"
    echo "Frontend: http://localhost:3000"
    echo "======================================"
    echo ""
    
    npm run dev
    
    # Kill backend when frontend exits
    kill $BACKEND_PID
else
    echo "Frontend not found. Backend running on http://localhost:8000"
    wait $BACKEND_PID
fi
