"""Deployment routing checks for the Vercel Flask entrypoint mode."""

import app as app_package


def test_vercel_app_serves_root_and_api_from_the_flask_app(tmp_path, monkeypatch):
    source_static_dir = tmp_path / "static"
    source_static_dir.mkdir(parents=True)
    (source_static_dir / "index.html").write_text(
        "<main>Vercel frontend</main>", encoding="utf-8"
    )
    css_dir = source_static_dir / "css"
    js_dir = source_static_dir / "js"
    css_dir.mkdir()
    js_dir.mkdir()
    (css_dir / "style.css").write_text("body { color: black; }", encoding="utf-8")
    (js_dir / "app.js").write_text("window.appLoaded = true;", encoding="utf-8")

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(app_package, "BASE_DIR", tmp_path)
    client = app_package.create_app().test_client()

    page = client.get("/")
    assert page.status_code == 200
    assert b"Vercel frontend" in page.data

    css = client.get("/static/css/style.css")
    assert css.status_code == 200
    assert css.mimetype == "text/css"
    assert b"color: black" in css.data

    javascript = client.get("/static/js/app.js")
    assert javascript.status_code == 200
    assert javascript.mimetype == "text/javascript"
    assert b"appLoaded" in javascript.data

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}

    chat = client.post("/api/chat", json={"message": ""})
    assert chat.status_code == 200
    assert chat.get_json()["intent"] == "empty"
