from __future__ import annotations

import json
from pathlib import Path

import httpx

from .config import get_api_key
from .schemas import CloneResult, FishAudioResult

BASE_URL = "https://api.fish.audio"


class FishAPIError(Exception):
    def __init__(self, category: str, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.category = category
        self.message = message
        self.uncertain = uncertain


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("reason") or payload)
    except (ValueError, json.JSONDecodeError):
        pass
    return response.text[:500] or f"HTTP {response.status_code}"


def _raise_status(response: httpx.Response, action: str) -> None:
    code = response.status_code
    message = _message(response)
    mapping = {
        401: ("authentication", "Fish API Key 无效或已过期"),
        402: ("insufficient_balance", "Fish 账户余额不足"),
        403: ("forbidden", "Fish 拒绝了该请求"),
        404: ("reference_not_found", "Fish 未找到该音色或资源"),
        413: ("file_too_large", "上传文件超过 Fish 限制"),
        422: ("validation", f"Fish 拒绝了请求参数：{message}"),
        429: ("rate_limited", "Fish 请求过于频繁，请稍后手动重试"),
    }
    category, friendly = mapping.get(
        code,
        ("provider_error", f"Fish {action}失败（HTTP {code}）：{message}"),
    )
    raise FishAPIError(category, friendly)


class FishClient:
    """Raw Fish API wrapper. Billable POSTs are deliberately never retried."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        key = get_api_key()
        if not key:
            raise FishAPIError("missing_api_key", "请先在设置中配置 Fish API Key")
        return {"Authorization": f"Bearer {key}"}

    async def check_connection(self) -> str:
        try:
            async with httpx.AsyncClient(
                base_url=BASE_URL,
                headers=self._headers(),
                timeout=httpx.Timeout(15.0),
                transport=self.transport,
            ) as client:
                response = await client.get("/wallet/self/api-credit")
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise FishAPIError("network", "无法连接 Fish API") from exc
        except httpx.TimeoutException as exc:
            raise FishAPIError("timeout", "Fish 连接检查超时") from exc
        if response.status_code != 200:
            _raise_status(response, "连接检查")
        payload = response.json()
        credit = payload.get("credit", "未知") if isinstance(payload, dict) else "未知"
        return f"连接成功，API credit：{credit}"

    async def tts(
        self,
        *,
        text: str,
        reference_id: str,
        model: str,
        output_format: str,
        speed: float | None,
    ) -> FishAudioResult:
        body: dict[str, object] = {
            "text": text,
            "reference_id": reference_id,
            "format": output_format,
        }
        if speed is not None:
            body["prosody"] = {
                "speed": speed,
                "volume": 0,
                "normalize_loudness": True,
            }
        headers = {**self._headers(), "model": model, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(
                base_url=BASE_URL,
                headers=headers,
                timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0),
                transport=self.transport,
            ) as client:
                response = await client.post("/v1/tts", json=body)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise FishAPIError("network", "连接 Fish 失败，请确认网络后手动重试") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            raise FishAPIError(
                "provider_result_uncertain",
                "请求结果不确定。Fish 可能已经收到请求，请确认后再手动决定是否重新生成。",
                uncertain=True,
            ) from exc
        if response.status_code != 200:
            _raise_status(response, "TTS")
        return FishAudioResult(
            content=response.content,
            content_type=response.headers.get("content-type", ""),
        )

    async def clone_voice(self, *, title: str, audio_paths: list[Path]) -> CloneResult:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        mime_map = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".opus": "audio/opus",
        }
        for path in audio_paths:
            files.append(
                ("voices", (path.name, path.read_bytes(), mime_map.get(path.suffix, "audio/wav")))
            )
        data = {
            "type": "tts",
            "title": title,
            "train_mode": "fast",
            "visibility": "private",
            "enhance_audio_quality": "true",
            "generate_sample": "false",
        }
        try:
            async with httpx.AsyncClient(
                base_url=BASE_URL,
                headers=self._headers(),
                timeout=httpx.Timeout(connect=15.0, read=180.0, write=120.0, pool=15.0),
                transport=self.transport,
            ) as client:
                response = await client.post("/model", data=data, files=files)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise FishAPIError("network", "连接 Fish 失败，角色与参考音频已保留") from exc
        except httpx.TimeoutException as exc:
            raise FishAPIError(
                "provider_result_uncertain",
                "克隆请求结果不确定，Fish 可能已创建资源；请先在 Fish 控制台确认。",
                uncertain=True,
            ) from exc
        if response.status_code != 201:
            _raise_status(response, "Voice Clone")
        payload = response.json()
        reference_id = payload.get("_id")
        if not reference_id:
            raise FishAPIError("invalid_response", "Fish 克隆响应缺少 reference_id")
        return CloneResult(
            reference_id=str(reference_id), state=str(payload.get("state", "created"))
        )


def validate_audio(content: bytes, content_type: str, output_format: str) -> None:
    if not content:
        raise FishAPIError("invalid_audio", "Fish 返回了空音频")
    lowered = content_type.lower()
    if "json" in lowered or content[:1] in {b"{", b"["}:
        raise FishAPIError("invalid_audio", "Fish 返回了 JSON，而不是音频")
    if len(content) < 16:
        raise FishAPIError("invalid_audio", "Fish 返回的音频过短或已损坏")
    signatures = {
        "mp3": content.startswith(b"ID3") or content[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"},
        "wav": content.startswith(b"RIFF") and content[8:12] == b"WAVE",
        "opus": content.startswith(b"OggS"),
        "pcm": True,
    }
    if not signatures.get(output_format, False) and not lowered.startswith("audio/"):
        raise FishAPIError("invalid_audio", f"Fish 返回内容不像有效的 {output_format} 音频")


def redact_secret(text: str) -> str:
    key = get_api_key()
    return text.replace(key, "[REDACTED]") if key else text
