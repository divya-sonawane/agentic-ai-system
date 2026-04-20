# Agentic AI System

A sophisticated multi-agent task execution framework powered by Claude AI. This system decomposes complex tasks into specialized agent workflows, executes them asynchronously, and streams results in real-time using Server-Sent Events (SSE).

## 🎯 Overview

The Agentic AI System uses a collaborative multi-agent architecture where each agent has a single, well-defined responsibility:

- **Planner**: Decomposes complex tasks into ordered, typed execution steps
- **Retriever**: Gathers relevant facts and context for tasks
- **Analyzer**: Performs deep reasoning over retrieved data
- **Validator**: Quality-checks analysis for consistency and completeness
- **Writer**: Synthesizes all context into polished final responses

All agents communicate via **shared memory** and support **async streaming** for real-time output delivery.

## ✨ Features

- **Task Decomposition**: Automatically breaks down complex tasks into manageable steps
- **Agent-Based Architecture**: Specialized agents for planning, retrieval, analysis, validation, and synthesis
- **Shared Memory System**: Agents accumulate context and findings for downstream processing
- **Real-Time Streaming**: SSE-based streaming for live task progress and results
- **Async/Await**: Fully asynchronous execution for high concurrency
- **Error Handling & Retries**: Built-in retry logic with exponential backoff
- **Queue Backends**: Support for both in-memory and Redis-based task queues
- **REST API**: Clean FastAPI interface for task management
- **Logging**: Comprehensive logging for debugging and monitoring

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI REST Server                        │
│  POST /tasks    GET /tasks/{id}/stream   GET /tasks/{id}... │
└──────────────────────────────┬──────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Orchestrator      │
                    │ (Task Executor)     │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
         ┌───────▼────┐   ┌───▼──────┐   ┌─▼──────────┐
         │ PlannAgent │   │  Agents  │   │   Shared   │
         │  (Planner) │   │ Registry │   │  Memory    │
         └────────────┘   └──────────┘   └────────────┘
                 │             │             │
         ┌───────┴──┬──────────┴─┬──────────┴────┐
         │          │            │               │
    ┌────▼──┐ ┌─────▼──┐  ┌────▼───┐  ┌─────▼────┐
    │Retriev│ │Analyzer│  │Validat │  │ Writer   │
    │ Agent │ │ Agent  │  │ Agent  │  │ Agent    │
    └───────┘ └────────┘  └────────┘  └──────────┘
         │          │            │               │
         └──────────┼────────────┼───────────────┘
                    │
         ┌──────────▼──────────┐
         │ Claude 3.5 Sonnet   │
         │ (Anthropic API)     │
         └─────────────────────┘
```

## 📦 Project Structure

```
agentic_ai_system/
├── agents/
│   ├── __init__.py
│   └── agents.py              # Agent implementations (Planner, Retriever, etc.)
├── api/
│   ├── __init__.py
│   └── app.py                 # FastAPI application & endpoints
├── core/
│   ├── __init__.py
│   ├── orchestrator.py        # Task execution orchestrator
│   └── queue.py               # Queue backends (memory & Redis)
├── .env                       # Environment variables (API keys)
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- An Anthropic API key (from [console.anthropic.com](https://console.anthropic.com))
- (Optional) Redis for distributed queue backend

### Installation

1. **Clone/Setup the project**:
   ```bash
   cd agentic_ai_system
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

### Configuration

1. **Create a `.env` file** in the project root:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
   QUEUE_BACKEND=memory          # or "redis"
   REDIS_HOST=localhost
   REDIS_PORT=6379
   ```

   Replace `YOUR_KEY_HERE` with your actual Anthropic API key.

2. **Verify the API key is loaded**:
   ```bash
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('ANTHROPIC_API_KEY')[:20] + '...')"
   ```

### Running the Server

Start the FastAPI development server:

```bash
uvicorn api.app:app --reload
```

The server will start at `http://127.0.0.1:8000`

Access the interactive API documentation:
- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc

## 📡 API Endpoints

### 1. Create a Task
**POST** `/tasks`

Start a new task execution.

**Request Body**:
```json
{
  "task": "Explain how neural networks work"
}
```

**Response**:
```json
{
  "task_id": "c760dafc-968b-430c-aa19-21d12c35a458",
  "status": "queued"
}
```

### 2. Stream Task Results
**GET** `/tasks/{task_id}/stream`

Stream live task progress and results using Server-Sent Events (SSE).

**Query Parameters**:
- `task` (optional): Task description for logging

**Response** (Server-Sent Events):
```
data: {"event": "connected", "task_id": "..."}
data: {"event": "step_started", "agent_type": "planner", "content": "📖 Planning..."}
data: {"event": "step_completed", "agent_type": "planner", "content": "..."}
...
data: {"event": "stream_end"}
```

**Example (cURL)**:
```bash
curl -N "http://127.0.0.1:8000/tasks/c760dafc-968b-430c-aa19-21d12c35a458/stream?task=explain+python"
```

**Example (JavaScript)**:
```javascript
const eventSource = new EventSource('/tasks/c760dafc-968b-430c-aa19-21d12c35a458/stream?task=explain%20python');
eventSource.onmessage = (event) => {
  console.log(JSON.parse(event.data));
};
eventSource.onerror = () => eventSource.close();
```

### 3. Get Task Status
**GET** `/tasks/{task_id}`

Retrieve the current status and metadata of a task.

**Response**:
```json
{
  "task_id": "c760dafc-968b-430c-aa19-21d12c35a458",
  "status": "completed",
  "steps": [
    {
      "step_id": "step-01-abc123",
      "agent_type": "planner",
      "status": "completed",
      "description": "Break task into steps"
    }
  ]
}
```

### 4. Health Check
**GET** `/health`

Check if the server is running.

**Response**:
```json
{
  "status": "healthy"
}
```

## 🤖 Agents Explained

### Planner Agent
- **Role**: Decomposes a user task into 3-6 concrete steps
- **Input**: User task description
- **Output**: Ordered list of `TaskStep` objects with agent types and descriptions
- **Example**: 
  - Input: "Explain quantum computing"
  - Output: [Retrieve quantum computing basics, Analyze key concepts, Validate accuracy, Write synthesis]

### Retriever Agent
- **Role**: Gathers relevant facts and context
- **Input**: Task + step description
- **Output**: List of facts, sources, and summary
- **Storage**: Accumulates facts in shared memory for other agents
- **Real-world**: Could integrate with vector DBs, web search, or RAG pipelines

### Analyzer Agent
- **Role**: Performs deep reasoning over retrieved data
- **Input**: Task + all gathered facts
- **Output**: Key insights, patterns, gaps, recommendations, confidence score
- **Storage**: Stores analysis results in shared memory

### Validator Agent
- **Role**: Quality-checks the analysis
- **Input**: Task + facts + analysis
- **Output**: Validity flag, issues found, corrections, confidence score
- **Purpose**: Ensures logical consistency and completeness

### Writer Agent
- **Role**: Synthesizes all context into a final response
- **Input**: Task + facts + analysis + validation
- **Output**: Comprehensive, well-structured, actionable response
- **Feature**: Supports streaming for real-time token delivery

## 🔄 Execution Flow

1. **Task Submission**: User sends a task via `POST /tasks`
2. **Queueing**: Task is queued in memory or Redis
3. **Planning**: Orchestrator calls PlannerAgent to decompose task
4. **Execution**: Steps are executed sequentially:
   - RetrieverAgent gathers facts
   - AnalyzerAgent reasons over data
   - ValidatorAgent checks quality
   - WriterAgent synthesizes output
5. **Streaming**: Events are streamed to client via SSE
6. **Completion**: Final response is stored and marked complete

## 🛠️ Development

### Adding a New Agent

1. Create a subclass of `BaseAgent`:
   ```python
   class CustomAgent(BaseAgent):
       agent_type = AgentType.CUSTOM  # Add to AgentType enum first
       name = "Custom"
       
       async def run(self, input_data: Dict, shared_memory: Dict) -> Dict[str, Any]:
           # Implementation
           return {"result": "..."}
   ```

2. Register in `AgentRegistry`:
   ```python
   registry.register(AgentType.CUSTOM, CustomAgent())
   ```

### Testing an Agent

```python
import asyncio
from agents.agents import RetrieverAgent

async def test():
    agent = RetrieverAgent()
    result = await agent.run(
        input_data={"task": "test", "description": "retrieve facts"},
        shared_memory={}
    )
    print(result)

asyncio.run(test())
```

### Debugging

Enable detailed logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check the uvicorn terminal output for agent execution logs and API errors.

## 📋 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | ^0.136.0 | Web framework |
| uvicorn | ^0.44.0 | ASGI server |
| httpx | ^0.28.0 | Async HTTP client |
| redis | ^7.4.0 | Redis Python client |
| rq | ^2.8.0 | Redis queue library |
| python-dotenv | ^1.2.2 | Environment variable management |

## 🔑 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required** Anthropic API key |
| `QUEUE_BACKEND` | `memory` | Queue implementation: `memory` or `redis` |
| `REDIS_HOST` | `localhost` | Redis server hostname |
| `REDIS_PORT` | `6379` | Redis server port |

## 📊 Example Workflow

### Request
```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"task": "What are the key differences between Python and JavaScript?"}'
```

### Response
```json
{
  "task_id": "d3f5c8a2-1b4e-4e9e-9c2f-8a3d5e6f7g8h"
}
```

### Stream Results
```bash
curl -N "http://127.0.0.1:8000/tasks/d3f5c8a2-1b4e-4e9e-9c2f-8a3d5e6f7g8h/stream?task=comparison"
```

### Stream Output
```
data: {"event": "connected", "task_id": "d3f5c8a2-1b4e-4e9e-9c2f-8a3d5e6f7g8h"}
data: {"event": "step_started", "agent_type": "planner", "content": "🧠 Analyzing your task and building an execution plan..."}
data: {"event": "step_completed", "agent_type": "planner", "content": "✓ Planned 4 steps"}
data: {"event": "step_started", "agent_type": "retriever", "content": "🔍 Gathering relevant information..."}
data: {"event": "step_completed", "agent_type": "retriever", "content": "✓ Retrieved 12 facts"}
data: {"event": "step_started", "agent_type": "analyzer", "content": "📊 Analyzing differences..."}
data: {"event": "step_completed", "agent_type": "analyzer", "content": "✓ Analysis complete with 8 key insights"}
data: {"event": "step_started", "agent_type": "validator", "content": "✓ All checks passed"}
data: {"event": "step_started", "agent_type": "writer", "content": "✍️ Writing comprehensive response..."}
data: {"event": "stream_end"}
```

## 🐛 Troubleshooting

### "400 Bad Request" from Anthropic API

**Problem**: API returns 400 error  
**Solution**: 
- Verify `ANTHROPIC_API_KEY` is correct and valid
- Check that the model name is valid (currently using `claude-3-5-sonnet-20241022`)
- Ensure API version in headers is correct (`anthropic-version: "2024-10-15"`)
- Check logs for detailed error message

### Module Not Found Errors

**Problem**: `ModuleNotFoundError: No module named 'agents'`  
**Solution**:
```bash
pip install -r requirements.txt
python -c "import agents"
```

### Redis Connection Failed

**Problem**: Can't connect to Redis queue backend  
**Solution**:
```bash
# Option 1: Use in-memory queue
export QUEUE_BACKEND=memory

# Option 2: Start Redis and verify connection
redis-cli ping  # Should return PONG
```

## 📝 Logging

Logs are printed to stdout with the format:
```
YYYY-MM-DD HH:MM:SS [LEVEL] module_name: message
```

Example:
```
2026-04-20 01:24:11,352 [ERROR] agents.agents: API Error (attempt 1): Status 400: {'type': 'error', ...}
```

## 🔐 Security Notes

- **API Key**: Never commit `.env` file to version control
- **CORS**: Currently allows all origins (`*`). Restrict in production:
  ```python
  CORSMiddleware(
      allow_origins=["https://yourdomain.com"],
      allow_methods=["POST", "GET"],
      allow_headers=["*"],
  )
  ```
- **Rate Limiting**: Consider adding rate limiting in production

## 🚀 Deployment

### Docker Deployment

Create a `Dockerfile`:
```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t agentic-ai .
docker run -e ANTHROPIC_API_KEY=sk-ant-... -p 8000:8000 agentic-ai
```

### Production Considerations

- Use Gunicorn/Uvicorn with multiple workers
- Enable Redis for distributed queue backend
- Add authentication to API endpoints
- Implement rate limiting
- Set up monitoring and alerting
- Use structured logging (JSON format)

## 📚 References

- [Anthropic API Documentation](https://docs.anthropic.com)
- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Server-Sent Events (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

## 📄 License

This project is provided as-is for educational and commercial use.

## 👨‍💻 Support

For issues, questions, or contributions:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review logs for error details
3. Verify environment configuration

---

**Last Updated**: April 20, 2026  
**Version**: 1.0.0
