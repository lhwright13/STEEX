"""Shared utilities for dashboard routes."""

import json
from pathlib import Path

HEARTBEAT_FILE = Path(__file__).resolve().parent.parent / "data" / "heartbeat.json"


def load_heartbeat():
    """Read the latest heartbeat.json."""
    try:
        with open(HEARTBEAT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def parse_json_field(record, field, target_key, default=None):
    """Parse a JSON string field into a Python object.

    Args:
        record: Dict to read from and write to.
        field: Key containing the JSON string.
        target_key: Key to store the parsed result under.
        default: Value when field is missing or unparseable (default: []).
    """
    if default is None:
        default = []
    if record.get(field):
        try:
            record[target_key] = json.loads(record[field])
        except (json.JSONDecodeError, TypeError):
            record[target_key] = default
    else:
        record[target_key] = default
