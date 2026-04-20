"""
Specialized Agents
Each agent has a single responsibility and communicates via shared memory.
All agents are async and support streaming where applicable.
"""
import os
import asyncio
import json
import uuid
import time
import httpx
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional
from core.orchestrator import AgentType, TaskStep
from dotenv import load_dotenv
load_dotenv()


logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-3-haiku-20240307"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def validate_api_key() -> bool:
    """
    Validate that ANTHROPIC_API_KEY is properly set.
    
    Returns:
        bool: True if API key is valid, False otherwise
        
    Raises:
        ValueError: If API key is missing or empty
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Please set it in your .env file or environment."
        )
    
    if not isinstance(api_key, str) or len(api_key) < 10:
        raise ValueError(
            "ANTHROPIC_API_KEY appears to be invalid (too short). "
            "Please verify your API key is correct."
        )
    
    if not api_key.startswith(("sk-ant-", "sk-")):
        logger.warning(
            "ANTHROPIC_API_KEY does not match expected format (sk-ant-* or sk-*). "
            "This may cause API errors."
        )
    
    return True


class BaseAgent(ABC):
    agent_type: AgentType
    name: str

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        raise NotImplementedError

    async def stream(self, input_data: Dict, shared_memory: Dict) -> AsyncGenerator[str, None]:
        result = await self.run(input_data, shared_memory)
        yield result.get("summary", "")

    async def _call_llm(self, system: str, user: str, max_tokens: int = 1024) -> str:
        """Call Anthropic API with retry logic."""
        # Validate API key before making any requests
        try:
            validate_api_key()
        except ValueError as e:
            raise ValueError(str(e))
        
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        ANTHROPIC_API_URL,
                        headers={
                            "Content-Type": "application/json",
                            "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                            "anthropic-version": "2023-06-01"
                        },
                        json={
                            "model": DEFAULT_MODEL,
                            "max_tokens": max_tokens,
                            "system": system,
                            "messages": [{"role": "user", "content": user}],
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["content"][0]["text"]
            except httpx.HTTPStatusError as e:
                error_msg = f"Status {e.response.status_code}: "
                try:
                    error_data = e.response.json()
                    error_msg += str(error_data)
                except:
                    error_msg += e.response.text
                logger.error(f"API Error (attempt {attempt+1}): {error_msg}")
                
                if e.response.status_code == 529 and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("LLM call failed after 3 attempts")

    async def _stream_llm(self, system: str, user: str, max_tokens: int = 2048) -> AsyncGenerator[str, None]:
        """Stream tokens from Anthropic API."""
        # Validate API key before making any requests
        try:
            validate_api_key()
        except ValueError as e:
            raise ValueError(str(e))
        
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                async with client.stream(
                    "POST",
                    ANTHROPIC_API_URL,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                        "anthropic-version": "2023-06-01"
                    },
                    json={
                        "model": DEFAULT_MODEL,
                        "max_tokens": max_tokens,
                        "stream": True,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            raw = line[6:]
                            if raw == "[DONE]":
                                break
                            try:
                                event = json.loads(raw)
                                if event.get("type") == "content_block_delta":
                                    delta = event.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield delta.get("text", "")
                            except json.JSONDecodeError:
                                continue
            except httpx.HTTPStatusError as e:
                error_msg = f"Status {e.response.status_code}: "
                try:
                    error_data = e.response.json()
                    error_msg += str(error_data)
                except:
                    error_msg += e.response.text
                logger.error(f"Streaming API Error: {error_msg}")
                raise


class PlannerAgent(BaseAgent):
    """
    Decomposes a complex task into ordered, typed steps.
    Returns a structured list of TaskStep objects.
    """
    agent_type = AgentType.PLANNER
    name = "Planner"

    SYSTEM = """You are a task planning agent. Given a user task, break it into 3-6 concrete steps.
Each step must be assigned to one of these agent types: retriever, analyzer, writer, validator.

Respond ONLY with valid JSON in this exact format:
{
  "steps": [
    {
      "agent_type": "retriever|analyzer|writer|validator",
      "description": "What this step does",
      "input_key": "what data this step needs from memory (or null)"
    }
  ]
}

Rules:
- retriever steps gather facts/data
- analyzer steps reason over data
- validator steps check correctness
- writer steps produce final output (always last)
- Keep descriptions concise (< 15 words)
"""

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        task = input_data["task"]
        raw = await self._call_llm(
            system=self.SYSTEM,
            user=f"Task: {task}",
            max_tokens=600,
        )

        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        parsed = json.loads(raw)
        steps = []
        for i, s in enumerate(parsed.get("steps", [])):
            try:
                atype = AgentType(s["agent_type"])
            except ValueError:
                atype = AgentType.ANALYZER

            steps.append(TaskStep(
                step_id=f"step-{i+1:02d}-{str(uuid.uuid4())[:6]}",
                agent_type=atype,
                description=s["description"],
                input_data={"task": task, "input_key": s.get("input_key")},
            ))

        # Filter out WRITER steps — writer runs separately at end
        steps = [s for s in steps if s.agent_type != AgentType.WRITER]

        shared_memory["original_task"] = task
        shared_memory["plan"] = [s.description for s in steps]
        return {"steps": steps, "summary": f"Planned {len(steps)} steps"}


class RetrieverAgent(BaseAgent):
    """
    Gathers relevant facts and context for the task.
    In production: integrates with vector DB, web search, or RAG pipeline.
    """
    agent_type = AgentType.RETRIEVER
    name = "Retriever"

    SYSTEM = """You are a research retrieval agent. Given a task and step description,
gather and synthesize relevant factual information. Be thorough and specific.
Return a JSON object with:
{
  "facts": ["fact1", "fact2", ...],
  "sources": ["source description 1", ...],
  "summary": "one-sentence summary of what was retrieved"
}
Respond ONLY with valid JSON."""

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        task = input_data.get("task", shared_memory.get("original_task", ""))
        description = input_data.get("description", "Retrieve relevant information")

        raw = await self._call_llm(
            system=self.SYSTEM,
            user=f"Task: {task}\nStep: {description}\nProvide relevant retrieved information.",
            max_tokens=600,
        )
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)

        # Accumulate facts in shared memory
        existing = shared_memory.get("retrieved_facts", [])
        shared_memory["retrieved_facts"] = existing + result.get("facts", [])
        shared_memory["sources"] = result.get("sources", [])

        return {**result, "summary": result.get("summary", "Retrieved information")}


class AnalyzerAgent(BaseAgent):
    """
    Performs deep reasoning over retrieved data.
    Produces structured analysis that the Writer can use.
    """
    agent_type = AgentType.ANALYZER
    name = "Analyzer"

    SYSTEM = """You are a critical analysis agent. Given a task and gathered facts,
produce a deep, structured analysis. Return JSON:
{
  "key_insights": ["insight1", "insight2", ...],
  "patterns": ["pattern or theme identified", ...],
  "gaps": ["missing information or caveats", ...],
  "recommendation": "main actionable conclusion",
  "summary": "one-sentence summary of the analysis"
}
Respond ONLY with valid JSON."""

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        task = shared_memory.get("original_task", input_data.get("task", ""))
        facts = shared_memory.get("retrieved_facts", [])
        facts_text = "\n".join(f"- {f}" for f in facts) if facts else "No facts retrieved yet."

        raw = await self._call_llm(
            system=self.SYSTEM,
            user=f"Task: {task}\n\nAvailable facts:\n{facts_text}\n\nAnalyze thoroughly.",
            max_tokens=800,
        )
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)

        shared_memory["analysis"] = result
        shared_memory["key_insights"] = result.get("key_insights", [])
        shared_memory["recommendation"] = result.get("recommendation", "")

        return {**result, "summary": result.get("summary", "Analysis complete")}


class ValidatorAgent(BaseAgent):
    """
    Quality-checks the analysis for logical consistency and completeness.
    Flags issues and suggests corrections.
    """
    agent_type = AgentType.VALIDATOR
    name = "Validator"

    SYSTEM = """You are a validation and quality-assurance agent.
Review the task, facts, and analysis for:
1. Logical consistency
2. Missing critical information
3. Unsupported claims
4. Actionability of recommendations

Return JSON:
{
  "is_valid": true/false,
  "issues": ["issue1", ...],
  "corrections": ["correction1", ...],
  "confidence_score": 0.0-1.0,
  "summary": "one-sentence validation summary"
}
Respond ONLY with valid JSON."""

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        task = shared_memory.get("original_task", "")
        analysis = shared_memory.get("analysis", {})
        facts = shared_memory.get("retrieved_facts", [])

        raw = await self._call_llm(
            system=self.SYSTEM,
            user=f"""Task: {task}
Facts: {json.dumps(facts[:10])}
Analysis: {json.dumps(analysis)}
Validate the above.""",
            max_tokens=500,
        )
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)

        shared_memory["validation"] = result
        shared_memory["confidence"] = result.get("confidence_score", 0.8)

        return {**result, "summary": result.get("summary", "Validation complete")}


class WriterAgent(BaseAgent):
    """
    Synthesizes all gathered context into a polished final response.
    Streams tokens for real-time delivery to the user.
    """
    agent_type = AgentType.WRITER
    name = "Writer"

    SYSTEM = """You are an expert synthesis and writing agent.
Using the task, facts, analysis, validation, and recommendations from memory,
produce a comprehensive, well-structured, actionable response.

Structure your response with:
- A clear opening statement
- Key findings (use bullet points)
- Detailed analysis
- Concrete recommendations
- A concise conclusion

Write in clear, professional prose. Be specific and actionable."""

    async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
        full_text = ""
        async for chunk in self.stream(input_data, shared_memory):
            full_text += chunk
        shared_memory["final_answer"] = full_text
        return {"final_answer": full_text, "summary": "Final response written"}

    async def stream(self, input_data: Dict, shared_memory: Dict) -> AsyncGenerator[str, None]:
        task = shared_memory.get("original_task", input_data.get("task", ""))
        facts = shared_memory.get("retrieved_facts", [])
        analysis = shared_memory.get("analysis", {})
        validation = shared_memory.get("validation", {})
        recommendation = shared_memory.get("recommendation", "")

        context = f"""Original Task: {task}

Retrieved Facts:
{chr(10).join(f'• {f}' for f in facts[:15])}

Analysis:
{json.dumps(analysis, indent=2)}

Validation Result: {validation.get('summary', 'N/A')}
Confidence: {shared_memory.get('confidence', 0.8):.0%}

Key Recommendation: {recommendation}"""

        full_response = ""
        async for chunk in self._stream_llm(
            system=self.SYSTEM,
            user=f"Synthesize a final comprehensive response for:\n\n{context}",
            max_tokens=2048,
        ):
            full_response += chunk
            yield chunk

        shared_memory["final_answer"] = full_response


class AgentRegistry:
    """
    Central registry mapping AgentType → agent instance.
    Supports lazy instantiation and dependency injection.
    """

    def __init__(self):
        self._agents: Dict[AgentType, BaseAgent] = {}
        self._register_defaults()

    def _register_defaults(self):
        self._agents = {
            AgentType.PLANNER: PlannerAgent(),
            AgentType.RETRIEVER: RetrieverAgent(),
            AgentType.ANALYZER: AnalyzerAgent(),
            AgentType.WRITER: WriterAgent(),
            AgentType.VALIDATOR: ValidatorAgent(),
        }

    def get(self, agent_type: AgentType) -> BaseAgent:
        agent = self._agents.get(agent_type)
        if agent is None:
            raise ValueError(f"No agent registered for type: {agent_type}")
        return agent

    def register(self, agent_type: AgentType, agent: BaseAgent):
        self._agents[agent_type] = agent
        logger.info(f"[Registry] Registered agent: {agent.name} ({agent_type.value})")

    def list_agents(self) -> List[str]:
        return [f"{at.value}: {a.name}" for at, a in self._agents.items()]
