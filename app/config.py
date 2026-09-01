from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
ALLOWED_FORMATS = {"mp3", "wav", "opus", "pcm"}
ALLOWED_MODELS = {"s2.1-pro", "s2.1-pro-free", "s2-pro", "s1"}
UPLOAD_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def resolve_data_dir(value: str | Path | None = None) -> Path:
    configured = value or os.getenv("TTS_WORKBENCH_DATA_DIR") or DEFAULT_DATA_DIR
    return Path(configured).expanduser().resolve()


def init_directories(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "characters").mkdir(exist_ok=True)
    (data_dir / "projects").mkdir(exist_ok=True)


def env_path() -> Path:
    return PROJECT_ROOT / ".env"


def load_secrets() -> None:
    load_dotenv(env_path(), override=True)


def get_api_key() -> str:
    return os.getenv("FISH_API_KEY", "").strip()


def masked_api_key() -> str:
    key = get_api_key()
    if not key:
        return "未配置"
    tail = key[-4:] if len(key) >= 4 else "****"
    return f"已配置 · sk-****{tail}"


def save_api_key(new_key: str) -> None:
    """Atomically update only FISH_API_KEY while preserving other .env lines."""
    path = env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"FISH_API_KEY={new_key.strip()}"
    output: list[str] = []
    replaced = False
    for line in lines:
        if re.match(r"^\s*(?:export\s+)?FISH_API_KEY\s*=", line):
            if not replaced:
                output.append(replacement)
                replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    os.environ["FISH_API_KEY"] = new_key.strip()


def validate_project_slug(slug: str) -> str:
    value = slug.strip()
    if not value or len(value) > 80:
        raise ValueError("目录 slug 必须为 1–80 个字符")
    if value in {".", ".."} or value.upper().split(".")[0] in WINDOWS_RESERVED:
        raise ValueError("该目录 slug 在 Windows 上不可用")
    if re.search(r"[<>:\"/\\|?*\x00-\x1f]", value):
        raise ValueError("目录 slug 含路径分隔符、控制字符或非法文件名字符")
    if value.endswith((".", " ")) or Path(value).is_absolute():
        raise ValueError("目录 slug 不是安全的相对目录名")
    return value


def project_directory(data_dir: Path, slug: str) -> Path:
    safe = validate_project_slug(slug)
    root = (data_dir / "projects").resolve()
    target = (root / safe).resolve()
    if target.parent != root:
        raise ValueError("项目路径越界")
    return target


def safe_filename_component(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    if not cleaned or cleaned.upper().split(".")[0] in WINDOWS_RESERVED:
        return fallback
    return cleaned[:80]


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def parsed_env() -> dict[str, str | None]:
    return dict(dotenv_values(env_path())) if env_path().exists() else {}
