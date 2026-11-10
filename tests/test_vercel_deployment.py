"""Deployment routing checks for the Vercel Flask entrypoint mode."""

import app as app_package


def test_vercel_app_serves_root_and_api_from_the_flask_app(tmp_path, monkeypatch):
    source_static_dir = tmp_path / "static"
    source_static_dir.mkdir(parents=True)
    (source_static_dir / "index.html").write_text(
        "<main>Vercel frontend</main>", encoding="utf-8"
    )

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_package, "BASE_DIR", tmp_path)
    client = app_package.create_app().test_client()

    page = client.get("/")
    assert page.status_code == 200
    assert b"Vercel frontend" in page.data

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}

    chat = client.post("/api/chat", json={"message": ""})
    assert chat.status_code == 200
    assert chat.get_json()["intent"] == "empty"
