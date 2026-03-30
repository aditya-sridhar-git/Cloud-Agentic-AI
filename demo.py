"""Minimal test to print agent results."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import logging
logging.disable(logging.CRITICAL)  # Silence all loggers

from cloud_agent.cloud.mock_provider import MockProvider
from cloud_agent.utils.config import load_config
from cloud_agent.main import CloudOpsAgent

config = load_config()
config['agent']['dry_run'] = True
provider = MockProvider()
agent = CloudOpsAgent(config, provider=provider)
results = agent.run_once()

logging.disable(logging.NOTSET)

print(f"TOTAL: {len(results)} actions planned (dry-run)")
print("-" * 70)
for r in results:
    tool = r.get("tool", "?")
    resource = r.get("resource", r.get("instance_id", r.get("volume_id", "?")))
    status = r.get("status", "?")
    reason = r.get("reason", "")[:60]
    print(f"  [{tool}] {resource} -> {status} | {reason}")
print("-" * 70)
