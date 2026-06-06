"""Shared serialization helper (P0-2)."""
import json


def _safe_json(obj) -> str:
    """Serialize to JSON, handling non-serializable types."""
    def default(o):
        if hasattr(o, "__dict__"):
            return {k: v for k, v in o.__dict__.items() if not k.startswith("_")}
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=default)
