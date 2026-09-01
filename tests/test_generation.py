from __future__ import annotations

import json
import uuid

from app.models import Character, Generation, Project

from .conftest import generation_form


def test_success_files_metadata_and_duplicate(client_bundle):
    client, app, fish, (project_id, character_id) = client_bundle
    request_id = str(uuid.uuid4())
    form = generation_form(app, project_id, character_id, request_id)
    response = client.post("/api/generate", data=form)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["sequence_number"] == 1
    assert fish.calls == 1

    duplicate = client.post("/api/generate", data=form)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert fish.calls == 1

    with app.state.db.Session() as session:
        generation = session.get(Generation, payload["id"])
        audio = app.state.data_dir / generation.output_audio_file
        metadata = app.state.data_dir / generation.output_metadata_file
        assert audio.is_file()
        assert metadata.is_file()
        data = json.loads(metadata.read_text(encoding="utf-8"))
        assert data["character"] == "Alice / Test"
        assert data["effective_text"].startswith("[克制但失望]")
        assert "api" not in json.dumps(data).lower()


def test_failed_and_uncertain_leave_records_without_files(client_bundle):
    client, app, fish, ids = client_bundle
    project_id, character_id = ids
    fish.mode = "failed"
    failed = client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    )
    assert failed.status_code == 409
    assert failed.json()["status"] == "failed"
    fish.mode = "uncertain"
    uncertain = client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    )
    assert uncertain.status_code == 409
    assert uncertain.json()["status"] == "uncertain"
    with app.state.db.Session() as session:
        rows = session.query(Generation).all()
        assert len(rows) == 2
        assert all(row.output_audio_file is None for row in rows)
        project = session.get(Project, project_id)
        assert project.next_sequence == 1


def test_sequence_not_reused_after_delete(client_bundle):
    client, app, fish, (project_id, character_id) = client_bundle
    ids = []
    for _ in range(3):
        response = client.post(
            "/api/generate",
            data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
        )
        ids.append(response.json()["id"])
    deleted = client.post(
        f"/history/{ids[1]}/delete",
        data={"csrf_token": app.state.csrf_token},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    fourth = client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    )
    assert fourth.json()["sequence_number"] == 4


def test_regenerate_preserves_original(client_bundle):
    client, app, fish, (project_id, character_id) = client_bundle
    first = client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    ).json()
    response = client.post(
        f"/history/{first['id']}/regenerate",
        data={"csrf_token": app.state.csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.db.Session() as session:
        rows = session.query(Generation).order_by(Generation.id).all()
        assert len(rows) == 2
        assert rows[0].id == first["id"]
        assert rows[1].sequence_number == 2
        assert rows[1].original_text == rows[0].original_text


def test_character_with_history_is_archived(client_bundle):
    client, app, fish, (project_id, character_id) = client_bundle
    client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    )
    response = client.post(
        f"/characters/{character_id}/delete",
        data={"csrf_token": app.state.csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.db.Session() as session:
        character = session.get(Character, character_id)
        generation = session.query(Generation).one()
        assert character.is_archived is True
        assert generation.character_name_snapshot == "Alice / Test"


def test_missing_key_is_human_readable(client_bundle, monkeypatch):
    client, app, fish, (project_id, character_id) = client_bundle
    from app.fish_client import FishClient

    monkeypatch.delenv("FISH_API_KEY", raising=False)
    app.state.fish_client = FishClient()
    response = client.post(
        "/api/generate",
        data=generation_form(app, project_id, character_id, str(uuid.uuid4())),
    )
    assert response.status_code == 409
    assert "配置 Fish API Key" in response.json()["error_message"]
