from fastapi import FastAPI
from orchestrator.orchestrator import run_pipeline

app = FastAPI()

@app.get("/")
def home():
    return {"message": "Agentic AI System Running !"}

@app.get("/run")
async def run_task(task: str):
    result = await run_pipeline(task)
    return {"result": result}