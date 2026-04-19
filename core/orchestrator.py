"""
Orchestrator: The brain of the agentic system.
Accepts a task, plans steps, assigns agents, streams results.
"""

import asyncio
import json
import uuid
import time
from typing import AsyncGenerator, Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class AgentType(str, Enum):
    PLANNER = "planner"
    RETRIEVER = "retriever"
    ANALYZER = "analyzer"
    WRITER = "writer"
    VALIDATOR = "validator"


@dataclass
class TaskStep:
    step_id: str
    agent_type: AgentType
    description: str
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]] = None
    status: StepStatus = StepStatus.PENDING
    retries: int = 0
    max_retries: int = 3
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["agent_type"] = self.agent_type.value
        d["status"] = self.status.value
        return d


@dataclass
class TaskContext:
    task_id: str
    original_task: str
    steps: List[TaskStep] = field(default_factory=list)
    shared_memory: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    final_output: Optional[str] = None


@dataclass
class StreamEvent:
    event_type: str       # step_planned | step_started | chunk | step_done | task_done | error
    task_id: str
    step_id: Optional[str] = None
    agent_type: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[Dict] = None

    def to_sse(self) -> str:
        payload = {
            "event": self.event_type,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "agent_type": self.agent_type,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": time.time(),
        }
        return f"data: {json.dumps(payload)}\n\n"


class Orchestrator:
    """
    Central coordinator. Responsibilities:
    - Receive task from user
    - Call Planner agent to decompose into steps
    - Dispatch steps to specialized agents via async queue
    - Stream partial results back to caller
    - Handle retries and failures
    """

    def __init__(self, agent_registry, message_queue):
        self.agent_registry = agent_registry
        self.queue = message_queue
        self.active_tasks: Dict[str, TaskContext] = {}

    async def run_task(self, task: str) -> AsyncGenerator[str, None]:
        """
        Entry point. Accepts a task string, yields SSE-formatted stream events.
        """
        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id=task_id, original_task=task)
        self.active_tasks[task_id] = ctx

        logger.info(f"[Orchestrator] Starting task {task_id}: {task[:60]}...")

        try:
            # ── Phase 1: Planning ──────────────────────────────────────
            yield StreamEvent(
                event_type="step_started",
                task_id=task_id,
                agent_type=AgentType.PLANNER.value,
                content="🧠 Analyzing your task and building an execution plan...",
            ).to_sse()

            planner = self.agent_registry.get(AgentType.PLANNER)
            steps_raw = await planner.run(
                {"task": task, "task_id": task_id},
                ctx.shared_memory,
            )
            steps: List[TaskStep] = steps_raw["steps"]
            ctx.steps = steps

            # Emit the plan
            for step in steps:
                yield StreamEvent(
                    event_type="step_planned",
                    task_id=task_id,
                    step_id=step.step_id,
                    agent_type=step.agent_type.value,
                    content=step.description,
                    metadata={"total_steps": len(steps)},
                ).to_sse()

            await asyncio.sleep(0.05)  # let client render the plan

            # ── Phase 2: Execute steps (with manual batching) ──────────
            # Group steps by dependency level (independent steps run in parallel)
            batches = self._build_batches(steps)

            for batch_idx, batch in enumerate(batches):
                # Enqueue all steps in this batch
                for step in batch:
                    await self.queue.enqueue(step.to_dict())

                # Run the batch concurrently, streaming results
                async for event in self._run_batch(batch, ctx, task_id):
                    yield event

            # ── Phase 3: Final synthesis ───────────────────────────────
            writer = self.agent_registry.get(AgentType.WRITER)
            yield StreamEvent(
                event_type="step_started",
                task_id=task_id,
                agent_type=AgentType.WRITER.value,
                content="✍️ Synthesizing final response...",
            ).to_sse()

            async for chunk in writer.stream(
                {"task": task, "memory": ctx.shared_memory},
                ctx.shared_memory,
            ):
                yield StreamEvent(
                    event_type="chunk",
                    task_id=task_id,
                    agent_type=AgentType.WRITER.value,
                    content=chunk,
                ).to_sse()

            ctx.final_output = ctx.shared_memory.get("final_answer", "")

            yield StreamEvent(
                event_type="task_done",
                task_id=task_id,
                content="✅ Task completed successfully.",
                metadata={
                    "steps_completed": len([s for s in steps if s.status == StepStatus.COMPLETED]),
                    "steps_failed": len([s for s in steps if s.status == StepStatus.FAILED]),
                },
            ).to_sse()

        except Exception as e:
            logger.error(f"[Orchestrator] Task {task_id} failed: {e}", exc_info=True)
            yield StreamEvent(
                event_type="error",
                task_id=task_id,
                content=f"Fatal error: {str(e)}",
            ).to_sse()
        finally:
            # Clean up after a delay
            asyncio.create_task(self._cleanup_task(task_id, delay=300))

    def _build_batches(self, steps: List[TaskStep]) -> List[List[TaskStep]]:
        """
        Manual batching logic: group steps that can run in parallel.
        Convention: steps with the same agent_type prefix can be batched.
        Retriever steps run first (batch 0), Analyzer next (batch 1).
        """
        order = {
            AgentType.RETRIEVER: 0,
            AgentType.ANALYZER: 1,
            AgentType.VALIDATOR: 2,
        }
        batches: Dict[int, List[TaskStep]] = {}
        for step in steps:
            level = order.get(step.agent_type, 1)
            batches.setdefault(level, []).append(step)
        return [batches[k] for k in sorted(batches.keys())]

    async def _run_batch(
        self,
        batch: List[TaskStep],
        ctx: TaskContext,
        task_id: str,
    ) -> AsyncGenerator[str, None]:
        """Run all steps in a batch concurrently, yield stream events."""

        async def run_one(step: TaskStep):
            events = []
            step.status = StepStatus.RUNNING
            step.started_at = time.time()
            events.append(
                StreamEvent(
                    event_type="step_started",
                    task_id=task_id,
                    step_id=step.step_id,
                    agent_type=step.agent_type.value,
                    content=f"▶ {step.description}",
                ).to_sse()
            )

            for attempt in range(step.max_retries + 1):
                try:
                    agent = self.agent_registry.get(step.agent_type)
                    result = await agent.run(step.input_data, ctx.shared_memory)
                    step.output_data = result
                    step.status = StepStatus.COMPLETED
                    step.completed_at = time.time()
                    # Merge result into shared memory
                    ctx.shared_memory.update(result)
                    events.append(
                        StreamEvent(
                            event_type="step_done",
                            task_id=task_id,
                            step_id=step.step_id,
                            agent_type=step.agent_type.value,
                            content=result.get("summary", "Done"),
                            metadata={"duration": round(step.completed_at - step.started_at, 2)},
                        ).to_sse()
                    )
                    break
                except Exception as e:
                    step.retries = attempt + 1
                    if attempt < step.max_retries:
                        step.status = StepStatus.RETRYING
                        wait = 2 ** attempt  # exponential backoff
                        logger.warning(f"Step {step.step_id} failed (attempt {attempt+1}), retrying in {wait}s: {e}")
                        events.append(
                            StreamEvent(
                                event_type="chunk",
                                task_id=task_id,
                                step_id=step.step_id,
                                agent_type=step.agent_type.value,
                                content=f"⚠️ Retrying ({attempt+1}/{step.max_retries})...",
                            ).to_sse()
                        )
                        await asyncio.sleep(wait)
                    else:
                        step.status = StepStatus.FAILED
                        step.error = str(e)
                        logger.error(f"Step {step.step_id} permanently failed: {e}")
                        events.append(
                            StreamEvent(
                                event_type="error",
                                task_id=task_id,
                                step_id=step.step_id,
                                agent_type=step.agent_type.value,
                                content=f"❌ Step failed after {step.max_retries} retries: {e}",
                            ).to_sse()
                        )
            return events

        # Run all steps in batch concurrently
        results = await asyncio.gather(*[run_one(s) for s in batch])
        for event_list in results:
            for ev in event_list:
                yield ev

    async def _cleanup_task(self, task_id: str, delay: int):
        await asyncio.sleep(delay)
        self.active_tasks.pop(task_id, None)
        logger.info(f"Cleaned up task {task_id}")

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        ctx = self.active_tasks.get(task_id)
        if not ctx:
            return None
        return {
            "task_id": ctx.task_id,
            "original_task": ctx.original_task,
            "steps": [s.to_dict() for s in ctx.steps],
            "final_output": ctx.final_output,
        }
