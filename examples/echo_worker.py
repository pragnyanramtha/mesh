"""A minimal managed-worker command for smoke tests and demos."""

import json
import sys

task = json.load(sys.stdin)
print(json.dumps({"summary": f"Received task {task['id']}: {task['title']}", "goal": task["goal"]}))

