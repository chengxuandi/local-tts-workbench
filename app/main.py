from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import threading
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import (
    ALLOWED_FORMATS,
    ALLOWED_MODELS,
    MAX_UPLOAD_BYTES,
    UPLOAD_EXTENSIONS,
    init_directories,
    load_secrets,
    masked_api_key,
    new_csrf_token,
    project_directory,
    resolve_data_dir,
    save_api_key,
    validate_project_slug,
)
from .database import Database, set_setting
from .fish_client import FishAPIError, FishClient, redact_secret
from .models import Character, Generation, Project
from .schemas import GenerationInput
from .services.generation import (
    delete_generation_files,
    effective_text,
    estimate_cost,
    generate_audio,
    utf8_size,
)

logger = logging.getLogger("tts_workbench")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _shutdown_process(app_instance: FastAPI) -> None:
    server = getattr(app_instance.state, "uvicorn_server", None)
    if server is not None:
        server.should_exit = True
    else:
        os.kill(os.getpid(), signal.SIGINT)


def _flash(message: str, level: str = "info") -> str:
    from urllib.parse import quote

    return f"?message={quote(message)}&level={level}"


def _ctx(request: Request, **values):
    base = {
        "request": request,
        "csrf_token": request.app.state.csrf_token,
        "message": request.query_params.get("message"),
        "level": request.query_params.get("level", "info"),
    }
    base.update(values)
    return base


def _check_csrf(request: Request, token: str) -> None:
    if token != request.app.state.csrf_token:
        raise HTTPException(403, "安全令牌无效，请刷新页面后重试")


def _safe_data_file(app: FastAPI, relative: str, expected_root: Path) -> Path:
    target = (app.state.data_dir / relative).resolve()
    root = expected_root.resolve()
    if target != root and root not in target.parents:
        raise HTTPException(403, "文件路径越界")
    return target


def _validate_upload(name: str, content: bytes) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 WAV、MP3、M4A、FLAC、OGG、OPUS 参考音频")
    if not content:
        raise ValueError("上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("单个参考音频不能超过 20 MB")
    signatures = {
        ".wav": content.startswith(b"RIFF") and content[8:12] == b"WAVE",
        ".mp3": content.startswith(b"ID3")
        or content[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"},
        ".flac": content.startswith(b"fLaC"),
        ".ogg": content.startswith(b"OggS"),
        ".opus": content.startswith(b"OggS"),
        ".m4a": len(content) > 12 and content[4:8] == b"ftyp",
    }
    if not signatures.get(suffix, False):
        raise ValueError(f"{suffix} 文件内容与扩展名不符")
    return suffix


def create_app(
    *,
    data_dir: str | Path | None = None,
    fish_client: FishClient | None = None,
) -> FastAPI:
    load_secrets()
    resolved_data = resolve_data_dir(data_dir)
    init_directories(resolved_data)
    db = Database(resolved_data / "app.db")
    db.initialize()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        db.close()

    app = FastAPI(title="Local TTS Workbench", lifespan=lifespan)
    app.state.data_dir = resolved_data
    app.state.db = db
    app.state.fish_client = fish_client or FishClient()
    app.state.csrf_token = new_csrf_token()
    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static"
    )

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                if origin.rstrip("/") not in {f"http://{host}", f"https://{host}"}:
                    return JSONResponse({"detail": "拒绝跨来源请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(Exception)
    async def safe_exception_handler(_request: Request, exc: Exception):
        logger.exception("Unhandled error: %s", redact_secret(str(exc)))
        return JSONResponse({"detail": "本地程序发生错误，请查看终端日志"}, status_code=500)

    @app.get("/", include_in_schema=False)
    async def home():
        return RedirectResponse("/generate")

    @app.get("/generate", response_class=HTMLResponse)
    async def generation_page(request: Request, copy: int | None = None):
        with db.Session() as session:
            settings = db.settings(session)
            projects = session.scalars(select(Project).order_by(Project.created_at)).all()
            characters = session.scalars(
                select(Character)
                .where(Character.is_archived.is_(False), Character.fish_reference_id.is_not(None))
                .order_by(Character.name)
            ).all()
            copied = session.get(Generation, copy) if copy else None
        return templates.TemplateResponse(
            request,
            "generate.html",
            _ctx(
                request,
                projects=projects,
                characters=characters,
                settings=settings,
                copied=copied,
                client_request_id=str(uuid.uuid4()),
                models=sorted(ALLOWED_MODELS),
                formats=sorted(ALLOWED_FORMATS),
            ),
        )

    @app.post("/api/generate")
    async def generation_api(
        request: Request,
        csrf_token: str = Form(...),
        client_request_id: str = Form(...),
        project_id: int = Form(...),
        character_id: int = Form(...),
        text: str = Form(...),
        emotion: str = Form(""),
        model: str = Form(...),
        speed: str = Form(""),
        output_format: str = Form(...),
    ):
        _check_csrf(request, csrf_token)
        try:
            uuid.UUID(client_request_id)
            parsed_speed = float(speed) if speed.strip() else None
            result, duplicate = await generate_audio(
                app,
                GenerationInput(
                    client_request_id=client_request_id,
                    project_id=project_id,
                    character_id=character_id,
                    text=text,
                    emotion=emotion,
                    model=model,
                    speed=parsed_speed,
                    output_format=output_format,
                ),
            )
        except (ValueError, FishAPIError) as exc:
            message = exc.message if isinstance(exc, FishAPIError) else str(exc)
            return JSONResponse({"detail": message}, status_code=400)
        status_code = 200 if result["status"] == "success" else 409
        return JSONResponse({**result, "duplicate": duplicate}, status_code=status_code)

    @app.get("/projects", response_class=HTMLResponse)
    async def projects_page(request: Request):
        with db.Session() as session:
            projects = session.scalars(select(Project).order_by(Project.created_at.desc())).all()
        return templates.TemplateResponse(
            request, "projects.html", _ctx(request, projects=projects)
        )

    @app.post("/projects")
    async def create_project(
        request: Request,
        csrf_token: str = Form(...),
        slug: str = Form(...),
        display_name: str = Form(...),
    ):
        _check_csrf(request, csrf_token)
        try:
            safe_slug = validate_project_slug(slug)
            if not display_name.strip():
                raise ValueError("显示名称不能为空")
            with db.Session.begin() as session:
                exists = session.scalar(select(Project).where(Project.slug == safe_slug))
                if exists:
                    raise ValueError("该项目 slug 已存在")
                project = Project(slug=safe_slug, display_name=display_name.strip())
                session.add(project)
            root = project_directory(resolved_data, safe_slug)
            (root / "audio").mkdir(parents=True, exist_ok=True)
            (root / "metadata").mkdir(exist_ok=True)
            return RedirectResponse("/projects" + _flash("项目已创建", "success"), 303)
        except ValueError as exc:
            return RedirectResponse("/projects" + _flash(str(exc), "error"), 303)

    @app.get("/characters", response_class=HTMLResponse)
    async def characters_page(request: Request):
        with db.Session() as session:
            characters = session.scalars(
                select(Character).order_by(Character.is_archived, Character.created_at.desc())
            ).all()
            settings = db.settings(session)
        return templates.TemplateResponse(
            request,
            "characters.html",
            _ctx(
                request,
                characters=characters,
                settings=settings,
                models=sorted(ALLOWED_MODELS),
                max_upload_mb=MAX_UPLOAD_BYTES // 1024 // 1024,
            ),
        )

    @app.post("/characters/reference")
    async def create_reference_character(
        request: Request,
        csrf_token: str = Form(...),
        name: str = Form(...),
        reference_id: str = Form(...),
        description: str = Form(""),
        default_emotion: str = Form(""),
        default_speed: str = Form(""),
        default_model: str = Form("s2.1-pro"),
    ):
        _check_csrf(request, csrf_token)
        if not name.strip() or not reference_id.strip():
            return RedirectResponse(
                "/characters" + _flash("角色名称和 reference_id 不能为空", "error"), 303
            )
        if default_model not in ALLOWED_MODELS:
            return RedirectResponse("/characters" + _flash("模型不受支持", "error"), 303)
        try:
            speed = float(default_speed) if default_speed.strip() else None
            if speed is not None and not 0.5 <= speed <= 2.0:
                raise ValueError
        except ValueError:
            return RedirectResponse("/characters" + _flash("默认语速须为 0.5–2.0", "error"), 303)
        with db.Session.begin() as session:
            session.add(
                Character(
                    name=name.strip(),
                    description=description.strip(),
                    fish_reference_id=reference_id.strip(),
                    clone_status="ready",
                    default_emotion=default_emotion.strip(),
                    default_speed=speed,
                    default_model=default_model,
                )
            )
        return RedirectResponse("/characters" + _flash("角色已创建", "success"), 303)

    @app.post("/characters/clone")
    async def create_clone_character(
        request: Request,
        csrf_token: str = Form(...),
        name: str = Form(...),
        description: str = Form(""),
        default_emotion: str = Form(""),
        default_speed: str = Form(""),
        default_model: str = Form("s2.1-pro"),
        voices: list[UploadFile] = File(...),  # noqa: B008 - FastAPI dependency marker
    ):
        _check_csrf(request, csrf_token)
        if not name.strip() or not 1 <= len(voices) <= 20:
            return RedirectResponse(
                "/characters" + _flash("角色名称必填，参考音频须为 1–20 个", "error"), 303
            )
        try:
            speed = float(default_speed) if default_speed.strip() else None
            if speed is not None and not 0.5 <= speed <= 2.0:
                raise ValueError("默认语速须为 0.5–2.0")
            payloads: list[tuple[str, bytes, str]] = []
            for upload in voices:
                content = await upload.read(MAX_UPLOAD_BYTES + 1)
                suffix = _validate_upload(upload.filename or "", content)
                payloads.append((upload.filename or "voice", content, suffix))
        except ValueError as exc:
            return RedirectResponse("/characters" + _flash(str(exc), "error"), 303)
        with db.Session.begin() as session:
            character = Character(
                name=name.strip(),
                description=description.strip(),
                clone_status="draft",
                default_emotion=default_emotion.strip(),
                default_speed=speed,
                default_model=default_model,
            )
            session.add(character)
            session.flush()
            character_id = character.id
        char_root = (resolved_data / "characters" / str(character_id)).resolve()
        char_root.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        for _original, content, suffix in payloads:
            target = char_root / f"{uuid.uuid4().hex}{suffix}"
            target.write_bytes(content)
            saved_paths.append(target)
        with db.Session.begin() as session:
            character = session.get(Character, character_id)
            character.local_reference_audio = str(saved_paths[0].relative_to(resolved_data))
        try:
            result = await app.state.fish_client.clone_voice(
                title=name.strip(), audio_paths=saved_paths
            )
            with db.Session.begin() as session:
                character = session.get(Character, character_id)
                character.fish_reference_id = result.reference_id
                character.clone_status = "ready"
                character.clone_error = None
            message, level = "私有 Voice Clone 已创建", "success"
        except FishAPIError as exc:
            with db.Session.begin() as session:
                character = session.get(Character, character_id)
                character.clone_status = "clone_failed"
                character.clone_error = exc.message
            message, level = f"克隆未完成：{exc.message}", "error"
        return RedirectResponse("/characters" + _flash(message, level), 303)

    @app.post("/characters/{character_id}/retry-clone")
    async def retry_clone(request: Request, character_id: int, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        with db.Session() as session:
            character = session.get(Character, character_id)
            if not character or not character.local_reference_audio:
                return RedirectResponse(
                    "/characters" + _flash("没有可用于重试的本地参考音频", "error"), 303
                )
            path = _safe_data_file(
                app,
                character.local_reference_audio,
                resolved_data / "characters" / str(character.id),
            )
            name = character.name
        try:
            result = await app.state.fish_client.clone_voice(title=name, audio_paths=[path])
            with db.Session.begin() as session:
                character = session.get(Character, character_id)
                character.fish_reference_id = result.reference_id
                character.clone_status = "ready"
                character.clone_error = None
            message, level = "私有 Voice Clone 已创建", "success"
        except FishAPIError as exc:
            with db.Session.begin() as session:
                character = session.get(Character, character_id)
                character.clone_status = "clone_failed"
                character.clone_error = exc.message
            message, level = exc.message, "error"
        return RedirectResponse("/characters" + _flash(message, level), 303)

    @app.post("/characters/{character_id}/delete")
    async def delete_character(request: Request, character_id: int, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        with db.Session.begin() as session:
            character = session.get(Character, character_id)
            if not character:
                raise HTTPException(404, "角色不存在")
            count = session.scalar(
                select(func.count())
                .select_from(Generation)
                .where(Generation.character_id == character_id)
            )
            if count:
                character.is_archived = True
                message = "角色已有历史记录，已安全归档"
            else:
                local_dir = resolved_data / "characters" / str(character_id)
                session.delete(character)
                message = "角色已删除"
        if not count and local_dir.exists():
            shutil.rmtree(local_dir)
        return RedirectResponse("/characters" + _flash(message, "success"), 303)

    @app.get("/characters/{character_id}/reference-audio")
    async def reference_audio(character_id: int):
        with db.Session() as session:
            character = session.get(Character, character_id)
            if not character or not character.local_reference_audio:
                raise HTTPException(404, "没有本地参考音频")
            target = _safe_data_file(
                app,
                character.local_reference_audio,
                resolved_data / "characters" / str(character_id),
            )
        if not target.is_file():
            raise HTTPException(404, "参考音频文件已缺失")
        return FileResponse(target)

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request, character_id: int | None = None):
        with db.Session() as session:
            query = select(Generation).order_by(Generation.created_at.desc())
            if character_id:
                query = query.where(Generation.character_id == character_id)
            generations = session.scalars(query).all()
            characters = session.scalars(select(Character).order_by(Character.name)).all()
        return templates.TemplateResponse(
            request,
            "history.html",
            _ctx(
                request,
                generations=generations,
                characters=characters,
                selected_character=character_id,
            ),
        )

    @app.post("/history/{generation_id}/regenerate")
    async def regenerate(request: Request, generation_id: int, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        with db.Session() as session:
            old = session.get(Generation, generation_id)
            if not old or not old.character_id:
                return RedirectResponse("/history" + _flash("原角色已不存在", "error"), 303)
            values = GenerationInput(
                client_request_id=str(uuid.uuid4()),
                project_id=old.project_id,
                character_id=old.character_id,
                text=old.original_text,
                emotion=old.emotion_description,
                model=old.model,
                speed=old.speed,
                output_format=old.format,
            )
        result, _ = await generate_audio(app, values)
        level = "success" if result["status"] == "success" else "error"
        message = "重新生成成功" if level == "success" else str(result["error_message"])
        return RedirectResponse("/history" + _flash(message, level), 303)

    @app.post("/history/{generation_id}/delete")
    async def delete_history(request: Request, generation_id: int, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        with db.Session() as session:
            generation = session.get(Generation, generation_id)
            if not generation:
                raise HTTPException(404, "记录不存在")
            try:
                delete_generation_files(app, generation)
            except OSError as exc:
                return RedirectResponse("/history" + _flash(f"文件删除失败：{exc}", "error"), 303)
            session.delete(generation)
            session.commit()
        return RedirectResponse("/history" + _flash("记录和文件已删除", "success"), 303)

    @app.get("/audio/{generation_id}")
    async def audio(generation_id: int):
        with db.Session() as session:
            generation = session.get(Generation, generation_id)
            if not generation or generation.status != "success" or not generation.output_audio_file:
                raise HTTPException(404, "音频不存在")
            project = session.get(Project, generation.project_id)
            target = _safe_data_file(
                app,
                generation.output_audio_file,
                project_directory(resolved_data, project.slug) / "audio",
            )
        if not target.is_file():
            raise HTTPException(404, "音频文件已缺失")
        return FileResponse(
            target, media_type=f"audio/{'mpeg' if target.suffix == '.mp3' else target.suffix[1:]}"
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        with db.Session() as session:
            settings = db.settings(session)
        return templates.TemplateResponse(
            request,
            "settings.html",
            _ctx(
                request,
                settings=settings,
                key_status=masked_api_key(),
                models=sorted(ALLOWED_MODELS),
                formats=sorted(ALLOWED_FORMATS),
            ),
        )

    @app.post("/settings")
    async def save_settings(
        request: Request,
        csrf_token: str = Form(...),
        api_key: str = Form(""),
        default_model: str = Form(...),
        price_per_m_utf8_bytes: float = Form(...),
        default_output_format: str = Form(...),
    ):
        _check_csrf(request, csrf_token)
        if default_model not in ALLOWED_MODELS or default_output_format not in ALLOWED_FORMATS:
            return RedirectResponse("/settings" + _flash("设置值无效", "error"), 303)
        if price_per_m_utf8_bytes < 0:
            return RedirectResponse("/settings" + _flash("预计单价不能为负数", "error"), 303)
        if api_key.strip():
            save_api_key(api_key.strip())
        with db.Session.begin() as session:
            set_setting(session, "default_model", default_model)
            set_setting(session, "price_per_m_utf8_bytes", str(price_per_m_utf8_bytes))
            set_setting(session, "default_output_format", default_output_format)
        return RedirectResponse("/settings" + _flash("设置已保存并立即生效", "success"), 303)

    @app.post("/settings/test-connection")
    async def test_connection(request: Request, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        try:
            message = await app.state.fish_client.check_connection()
            level = "success"
        except FishAPIError as exc:
            message, level = exc.message, "error"
        return RedirectResponse("/settings" + _flash(message, level), 303)

    @app.get("/api/preview")
    async def preview(text: str = "", emotion: str = "", model: str = "s2.1-pro"):
        final = effective_text(text, emotion, model)
        with db.Session() as session:
            price = float(db.settings(session)["price_per_m_utf8_bytes"])
        count = utf8_size(final)
        return {
            "effective_text": final,
            "utf8_bytes": count,
            "estimated_cost_usd": estimate_cost(count, price),
        }

    @app.post("/shutdown", response_class=HTMLResponse)
    async def shutdown(request: Request, csrf_token: str = Form(...)):
        _check_csrf(request, csrf_token)
        timer = threading.Timer(0.75, _shutdown_process, args=(app,))
        timer.daemon = True
        timer.start()
        return HTMLResponse(
            """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
            <title>Local TTS Workbench 已退出</title>
            <style>body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;
            max-width:560px;margin:15vh auto;padding:32px;color:#1c2430}
            div{border:1px solid #dfe3e8;border-radius:10px;padding:28px}</style>
            <div><h1>程序已退出</h1><p>现在可以关闭这个浏览器页面。</p></div></html>"""
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    url = "http://127.0.0.1:8000"
    print("Local TTS Workbench")
    print(url)
    if "--open" in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Could not open browser")
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, reload=False)
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    server.run()


if __name__ == "__main__":
    run()
