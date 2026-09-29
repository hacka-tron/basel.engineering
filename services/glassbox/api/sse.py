"""Encode one JSON payload as an SSE frame."""

import json


def frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
