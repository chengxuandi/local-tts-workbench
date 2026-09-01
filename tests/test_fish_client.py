from __future__ import annotations

import httpx
import pytest

from app.fish_client import FishAPIError, FishClient

from .conftest import fake_mp3


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, "authentication"),
        (402, "insufficient_balance"),
        (404, "reference_not_found"),
        (429, "rate_limited"),
        (500, "provider_error"),
    ],
)
async def test_tts_status_mapping_and_no_retry(monkeypatch, status, category):
    monkeypatch.setenv("FISH_API_KEY", "sk-test")
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"message": "provider detail"}, request=request)

    client = FishClient(httpx.MockTransport(handler))
    with pytest.raises(FishAPIError) as captured:
        await client.tts(
            text="测试", reference_id="ref", model="s2.1-pro", output_format="mp3", speed=None
        )
    assert captured.value.category == category
    assert calls == 1


async def test_tts_success_sends_supported_fields(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "sk-test")
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(
            200, content=fake_mp3(), headers={"content-type": "audio/mpeg"}, request=request
        )

    result = await FishClient(httpx.MockTransport(handler)).tts(
        text="测试", reference_id="ref", model="s2.1-pro", output_format="mp3", speed=1.2
    )
    assert result.content.startswith(b"ID3")
    assert seen["request"].headers["model"] == "s2.1-pro"
    assert b'"prosody"' in seen["request"].content
    assert b'"speed":1.2' in seen["request"].content


async def test_timeout_uncertain_and_single_attempt(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "sk-test")
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("late", request=request)

    with pytest.raises(FishAPIError) as captured:
        await FishClient(httpx.MockTransport(handler)).tts(
            text="测试", reference_id="ref", model="s2.1-pro", output_format="mp3", speed=None
        )
    assert captured.value.uncertain is True
    assert calls == 1


async def test_clone_private_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("FISH_API_KEY", "sk-test")
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF" + b"\x00" * 40)
    calls = []

    def success_handler(request):
        calls.append(request)
        return httpx.Response(201, json={"_id": "voice-id", "state": "created"}, request=request)

    result = await FishClient(httpx.MockTransport(success_handler)).clone_voice(
        title="Alice", audio_paths=[voice]
    )
    assert result.reference_id == "voice-id"
    body = calls[0].content
    assert b'name="visibility"' in body and b"private" in body
    assert b'name="generate_sample"' in body and b"false" in body
    assert len(calls) == 1

    failures = 0

    def fail_handler(request):
        nonlocal failures
        failures += 1
        return httpx.Response(500, json={"message": "nope"}, request=request)

    with pytest.raises(FishAPIError):
        await FishClient(httpx.MockTransport(fail_handler)).clone_voice(
            title="Alice", audio_paths=[voice]
        )
    assert failures == 1


async def test_free_key_check(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "sk-test")

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/wallet/self/api-credit"
        return httpx.Response(200, json={"credit": "3.50"}, request=request)

    message = await FishClient(httpx.MockTransport(handler)).check_connection()
    assert "3.50" in message
