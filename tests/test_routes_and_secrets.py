from __future__ import annotations


def test_smoke_pages_and_static(client_bundle):
    client, app, _fish, _ids = client_bundle
    for path in ["/generate", "/projects", "/characters", "/history", "/settings"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "Local TTS Workbench" in response.text
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "--primary" in css.text
    assert client.get("/audio/999999").status_code == 404


def test_settings_never_render_full_key(client_bundle, monkeypatch):
    client, _app, _fish, _ids = client_bundle
    secret = "test-secret-value-9876"
    monkeypatch.setenv("FISH_API_KEY", secret)
    html = client.get("/settings").text
    assert secret not in html
    assert "sk-****9876" in html
    assert secret not in client.get("/api/preview?text=x").text


def test_same_origin_guard_and_csrf(client_bundle):
    client, app, _fish, _ids = client_bundle
    blocked = client.post(
        "/projects",
        data={"csrf_token": app.state.csrf_token, "slug": "x", "display_name": "X"},
        headers={"Origin": "https://evil.example"},
    )
    assert blocked.status_code == 403
    bad_csrf = client.post(
        "/projects", data={"csrf_token": "bad", "slug": "x", "display_name": "X"}
    )
    assert bad_csrf.status_code == 403


def test_duplicate_slug_is_explicit(client_bundle):
    client, app, _fish, _ids = client_bundle
    response = client.post(
        "/projects",
        data={
            "csrf_token": app.state.csrf_token,
            "slug": "chapter_01",
            "display_name": "重复",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "level=error" in response.headers["location"]
