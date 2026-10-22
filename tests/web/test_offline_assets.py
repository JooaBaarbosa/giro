from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "giro" / "web" / "templates"


def test_templates_load_nothing_from_external_hosts():
    for template in TEMPLATES.glob("*.html"):
        html = template.read_text(encoding="utf-8")
        for host in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com"):
            assert host not in html, f"{template.name} carrega {host}"


def test_vendored_scripts_are_served(client):
    for path in ("/static/vendor/htmx-2.0.4.min.js", "/static/vendor/chart-4.4.7.umd.min.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.content) > 10_000, path
