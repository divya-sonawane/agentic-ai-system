"""
FastAPI Application
Exposes:
  POST /tasks           → Start a new task, returns task_id
  GET  /tasks/{id}/stream → SSE stream of agent events
  GET  /tasks/{id}/status → Task status JSON
  GET  /health          → Health check
"""

import asyncio
import logging
import os
import sys

# Add parent to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from core.orchestrator import Orchestrator
from core.queue import create_queue, InMemoryQueue
from agents.agents import AgentRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── App Setup ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic AI System",
    description="Multi-step task execution with specialized agents and async streaming",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dependency Injection ───────────────────────────────────────────────────────
queue_backend = os.getenv("QUEUE_BACKEND", "memory")
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", "6379"))

if queue_backend == "redis":
    message_queue = create_queue("redis", host=redis_host, port=redis_port)
else:
    message_queue = create_queue("memory")

agent_registry = AgentRegistry()
orchestrator = Orchestrator(agent_registry=agent_registry, message_queue=message_queue)

logger.info(f"Started with queue backend: {queue_backend}")
logger.info(f"Registered agents: {agent_registry.list_agents()}")


# ── Request / Response Models ──────────────────────────────────────────────────
class TaskRequest(BaseModel):
    task: str
    priority: int = 1  # 1 (normal) to 5 (critical)

class TaskResponse(BaseModel):
    task_id: str
    message: str
    stream_url: str


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    queue_size = await message_queue.queue_size()
    return {
        "status": "ok",
        "queue_backend": queue_backend,
        "queue_size": queue_size,
        "active_tasks": len(orchestrator.active_tasks),
        "agents": agent_registry.list_agents(),
    }


@app.post("/tasks", response_model=TaskResponse)
async def create_task(req: TaskRequest):
    if not req.task.strip():
        raise HTTPException(status_code=400, detail="Task cannot be empty")
    if len(req.task) > 2000:
        raise HTTPException(status_code=400, detail="Task too long (max 2000 chars)")

    # We start streaming lazily — just return the stream URL
    import uuid
    task_id = str(uuid.uuid4())
    # Pre-register empty context so status endpoint works immediately
    return TaskResponse(
        task_id=task_id,
        message="Task accepted. Connect to stream_url to begin.",
        stream_url=f"/tasks/{task_id}/stream?task={req.task}",
    )


@app.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, task: str = ""):
    """
    SSE endpoint. Streams agent events as they happen.
    The client should connect here immediately after POST /tasks.
    """
    if not task.strip():
        raise HTTPException(status_code=400, detail="task query param required")

    async def event_generator():
        # Initial heartbeat
        yield "data: {\"event\": \"connected\", \"task_id\": \"" + task_id + "\"}\n\n"
        await asyncio.sleep(0.01)

        async for sse_chunk in orchestrator.run_task(task):
            yield sse_chunk
            await asyncio.sleep(0)  # yield control to event loop

        # Terminal signal for clients
        yield "data: {\"event\": \"stream_end\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    status = orchestrator.get_task_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return JSONResponse(content=status)


@app.get("/agents")
async def list_agents():
    return {"agents": agent_registry.list_agents()}


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "prod") == "dev",
        log_level="info",
    )
