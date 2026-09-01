from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GenerationInput:
    client_request_id: str
    project_id: int
    character_id: int
    text: str
    emotion: str
    model: str
    speed: float | None
    output_format: str


@dataclass(slots=True)
class FishAudioResult:
    content: bytes
    content_type: str


@dataclass(slots=True)
class CloneResult:
    reference_id: str
    state: str
