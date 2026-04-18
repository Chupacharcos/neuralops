from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime


class NeuralOpsState(TypedDict):
    pending_confirmations: List[Dict[str, Any]]
    demo_failures: Dict[str, int]        # slug -> consecutive failures
    service_metrics: Dict[str, Any]      # port -> latency history
    active_sessions: List[Dict[str, Any]]
    last_errors: Dict[str, str]          # agent_name -> last error


def default_state() -> NeuralOpsState:
    return {
        "pending_confirmations": [],
        "demo_failures": {},
        "service_metrics": {},
        "active_sessions": [],
        "last_errors": {},
    }
