#!/usr/bin/env bash
# Start the Competitive Analysis Agent web app (FastAPI backend + React frontend).
# Backend: http://localhost:8000   Frontend: http://localhost:5173
set -e
cd "$(dirname "$0")"

# Backend (FastAPI)
.venv/bin/uvicorn api:app --reload --port 8000 &
BACK=$!

# Frontend (Vite dev server)
( cd frontend && npm run dev ) &
VITE=$!

# Stop both on Ctrl-C.
trap "kill $BACK $VITE 2>/dev/null" EXIT INT TERM

echo ""
echo "  Backend : http://localhost:8000"
echo "  Frontend: http://localhost:5173   <-- open this"
echo ""
wait
