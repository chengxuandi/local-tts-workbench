from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Character, Project
from app.schemas import CloneResult, FishAudioResult


def fake_mp3() -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x15" + b"audio-payload-for-tests" * 3


@dataclass
class FakeFishClient:
    mode: str = "success"
    calls: int = 0
    clone_calls: int = 0

    async def tts(self, **_kwargs):
        from app.fish_client import FishAPIError

        self.calls += 1
        if self.mode == "failed":
            raise FishAPIError("rate_limited", "Fish 请求过于频繁")
        if self.mode == "uncertain":
            raise FishAPIError("provider_result_uncertain", "结果不确定", uncertain=True)
        return FishAudioResult(fake_mp3(), "audio/mpeg")

    async def clone_voice(self, **_kwargs):
        self.clone_calls += 1
        return CloneResult("ref-cloned", "created")

    async def check_connection(self):
        return "连接成功"


@pytest.fixture
def app_bundle(tmp_path):
    fish = FakeFishClient()
    app = create_app(data_dir=tmp_path / "data", fish_client=fish)
    with app.state.db.Session.begin() as session:
        project = Project(slug="chapter_01", display_name="第一章")
        character = Character(
            name="Alice / Test",
            fish_reference_id="ref-123",
            clone_status="ready",
            default_model="s2.1-pro",
        )
        session.add_all([project, character])
        session.flush()
        ids = (project.id, character.id)
    return app, fish, ids


@pytest.fixture
def client_bundle(app_bundle):
    app, fish, ids = app_bundle
    with TestClient(app) as client:
        yield client, app, fish, ids


def generation_form(app, project_id, character_id, request_id):
    return {
        "csrf_token": app.state.csrf_token,
        "client_request_id": request_id,
        "project_id": str(project_id),
        "character_id": str(character_id),
        "text": "测试。[sigh] 继续。",
        "emotion": "克制但失望",
        "model": "s2.1-pro",
        "speed": "1.1",
        "output_format": "mp3",
    }
