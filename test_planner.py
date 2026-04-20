"""
Test script to demonstrate running the PlannerAgent
"""
import asyncio
import json
from agents.agents import PlannerAgent
from dotenv import load_dotenv

load_dotenv()


async def run_planner(task: str):
    """Run the PlannerAgent with a given task."""
    print(f"\n📋 Running Planner Agent for task: {task}\n")
    
    agent = PlannerAgent()
    shared_memory = {}
    
    try:
        result = await agent.run(
            input_data={"task": task},
            shared_memory=shared_memory
        )
        
        print("✅ Success!")
        print(f"Summary: {result.get('summary')}\n")
        
        # Display steps
        steps = result.get("steps", [])
        print(f"Generated {len(steps)} steps:")
        for i, step in enumerate(steps, 1):
            print(f"\n  Step {i}:")
            print(f"    Type: {step.agent_type.value}")
            print(f"    Description: {step.description}")
            print(f"    ID: {step.step_id}")
        
        print(f"\nShared Memory State:")
        print(f"  Plan: {shared_memory.get('plan', [])}")
        print(f"  Original Task: {shared_memory.get('original_task', 'N/A')}")
        
        return result
        
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    # Test with a sample task
    task = "Analyze the impact of AI on software development and provide recommendations for developers"
    asyncio.run(run_planner(task))
