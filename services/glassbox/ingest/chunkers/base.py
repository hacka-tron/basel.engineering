"""Shared chunk type used by every chunker (Markdown, Terraform, YAML, code)."""

from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    source_path: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    token_count: int
