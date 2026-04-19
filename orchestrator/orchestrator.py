from planner.planner import planner
from agents.retriever import retriever
from agents.analyzer import analyzer
from agents.writer import writer

async def run_pipeline(task):
    steps = planner(task)
    data = None

    for step in steps:
        if step == "retrieve":
            data = retriever(task)
        elif step == "analyze":
            data = analyzer(data)
        elif step == "write":
            data = writer(data)

    return data