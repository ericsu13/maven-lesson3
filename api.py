"""FastAPI backend for the Competitive Analysis Agent web UI.

Endpoints:
- POST /api/analyze  {company}          -> runs Agents 1-4, returns the plan
- POST /api/submit   {company, plan}    -> runs Agent 5, writes to Salesforce

Results are always returned with HTTP 200 and a {"status": "PASS"|"FAIL", ...}
body so the frontend can render success and failure uniformly.

Run:  .venv/bin/uvicorn api:app --reload --port 8000
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import pipeline

app = FastAPI(title="Competitive Analysis Agent")

# Allow the Vite dev server (and local variants) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    company: str
    use_memory: bool = True


class SubmitRequest(BaseModel):
    company: str
    plan: dict
    transport: str = "stdio"  # "stdio" | "http" — which MCP transport to use


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """Stream progress events (Server-Sent Events) while Agents 1-4 run."""

    def events():
        for event in pipeline.analyze_stream(req.company, use_memory=req.use_memory):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/submit")
def submit(req: SubmitRequest):
    return pipeline.submit(req.company, req.plan, transport=req.transport)


@app.get("/api/mcp/health")
def mcp_health(transport: str = "stdio"):
    """Preflight for the UI's transport toggle: is the MCP server reachable?"""
    return pipeline.mcp_health(transport)


@app.get("/api/health")
def health():
    return {"status": "ok"}
