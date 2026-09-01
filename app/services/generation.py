from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import ALLOWED_FORMATS, ALLOWED_MODELS, project_directory, safe_filename_component
from ..fish_client import FishAPIError, validate_audio
from ..models import Character, Generation, Project
from ..schemas import GenerationInput

_sequence_lock = threading.Lock()


def effective_text(text: str, emotion: str, model: str) -> str:
    body = text.strip()
    cue = emotion.strip().replace("\r\n", " ").replace("\n", " ")
    if not cue:
        return body
    if model == "s1":
        return f"({cue})\n{body}"
    return f"[{cue}]\n{body}"


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def estimate_cost(byte_count: int, price_per_m: float) -> float:
    return byte_count / 1_000_000 * price_per_m


def _serialize(generation: Generation) -> dict[str, object]:
    return {
        "id": generation.id,
        "status": generation.status,
        "sequence_number": generation.sequence_number,
        "error_category": generation.error_category,
        "error_message": generation.error_message,
        "audio_url": f"/audio/{generation.id}" if generation.status == "success" else None,
        "utf8_bytes": generation.utf8_bytes,
        "estimated_cost_usd": generation.estimated_cost_usd,
    }


async def generate_audio(app, values: GenerationInput) -> tuple[dict[str, object], bool]:
    db = app.state.db
    with db.Session() as session:
        existing = session.scalar(
            select(Generation).where(Generation.client_request_id == values.client_request_id)
        )
        if existing:
            return _serialize(existing), True
        project = session.get(Project, values.project_id)
        character = session.get(Character, values.character_id)
        if not project:
            raise ValueError("项目不存在")
        if not character or not character.fish_reference_id:
            raise ValueError("角色不存在或尚未配置 reference_id")
        if values.model not in ALLOWED_MODELS:
            raise ValueError("模型不受支持")
        if values.output_format not in ALLOWED_FORMATS:
            raise ValueError("输出格式不受支持")
        if not values.text.strip():
            raise ValueError("正文不能为空")
        if values.speed is not None and not 0.5 <= values.speed <= 2.0:
            raise ValueError("语速必须在 0.5–2.0 之间")
        final_text = effective_text(values.text, values.emotion, values.model)
        settings = db.settings(session)
        byte_count = utf8_size(final_text)
        price = float(settings["price_per_m_utf8_bytes"])
        generation = Generation(
            client_request_id=values.client_request_id,
            project_id=project.id,
            character_id=character.id,
            character_name_snapshot=character.name,
            original_text=values.text.strip(),
            emotion_description=values.emotion.strip(),
            effective_fish_text=final_text,
            model=values.model,
            speed=values.speed,
            format=values.output_format,
            utf8_bytes=byte_count,
            estimated_cost_usd=estimate_cost(byte_count, price),
            status="generating",
        )
        session.add(generation)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(Generation).where(Generation.client_request_id == values.client_request_id)
            )
            if existing:
                return _serialize(existing), True
            raise
        generation_id = generation.id
        reference_id = character.fish_reference_id
        character_name = character.name
        project_slug = project.slug

    try:
        result = await app.state.fish_client.tts(
            text=final_text,
            reference_id=reference_id,
            model=values.model,
            output_format=values.output_format,
            speed=values.speed,
        )
        validate_audio(result.content, result.content_type, values.output_format)
    except FishAPIError as exc:
        with db.Session.begin() as session:
            generation = session.get(Generation, generation_id)
            generation.status = "uncertain" if exc.uncertain else "failed"
            generation.error_category = exc.category
            generation.error_message = exc.message
        with db.Session() as session:
            return _serialize(session.get(Generation, generation_id)), False

    project_root = project_directory(app.state.data_dir, project_slug)
    audio_root = project_root / "audio"
    metadata_root = project_root / "metadata"
    audio_root.mkdir(parents=True, exist_ok=True)
    metadata_root.mkdir(parents=True, exist_ok=True)
    temp_path = audio_root / f".tmp-{generation_id}-{uuid.uuid4().hex}"
    try:
        with temp_path.open("xb") as handle:
            handle.write(result.content)
            handle.flush()
            os.fsync(handle.fileno())
        with _sequence_lock:
            with db.Session.begin() as session:
                project = session.get(Project, values.project_id)
                generation = session.get(Generation, generation_id)
                sequence = project.next_sequence
                project.next_sequence += 1
                generation.sequence_number = sequence
        component = safe_filename_component(character_name, f"character-{values.character_id}")
        stem = f"{sequence:03d}_{component}"
        audio_path = audio_root / f"{stem}.{values.output_format}"
        metadata_path = metadata_root / f"{stem}.json"
        os.replace(temp_path, audio_path)
        metadata = {
            "generation_id": generation_id,
            "sequence_number": sequence,
            "character": character_name,
            "text": values.text.strip(),
            "emotion": values.emotion.strip(),
            "effective_text": final_text,
            "model": values.model,
            "speed": values.speed,
            "format": values.output_format,
            "utf8_bytes": byte_count,
            "estimated_cost_usd": estimate_cost(byte_count, price),
        }
        metadata["created_at"] = generation.created_at.isoformat()
        meta_temp = metadata_root / f".tmp-{generation_id}-{uuid.uuid4().hex}.json"
        meta_temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(meta_temp, metadata_path)
        with db.Session.begin() as session:
            generation = session.get(Generation, generation_id)
            generation.output_audio_file = str(audio_path.relative_to(app.state.data_dir))
            generation.output_metadata_file = str(metadata_path.relative_to(app.state.data_dir))
            generation.status = "success"
        with db.Session() as session:
            return _serialize(session.get(Generation, generation_id)), False
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        if "audio_path" in locals():
            audio_path.unlink(missing_ok=True)
        if "metadata_path" in locals():
            metadata_path.unlink(missing_ok=True)
        with db.Session.begin() as session:
            generation = session.get(Generation, generation_id)
            generation.status = "failed"
            generation.error_category = "local_storage"
            generation.error_message = f"Fish 已返回音频，但本地保存失败：{exc}"
        with db.Session() as session:
            return _serialize(session.get(Generation, generation_id)), False


def delete_generation_files(app, generation: Generation) -> None:
    paths = [generation.output_audio_file, generation.output_metadata_file]
    resolved: list[Path] = []
    root = app.state.data_dir.resolve()
    for relative in paths:
        if not relative:
            continue
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise OSError("拒绝删除数据目录以外的文件")
        resolved.append(candidate)
    for path in resolved:
        if path.exists():
            path.unlink()
