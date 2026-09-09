import io
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("FILE_MANAGER_DATA", tmp)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def upload(client, name, data=b"hello", ctype="text/plain"):
    return client.post(
        "/api/files",
        data={"file": (io.BytesIO(data), name, ctype)},
        content_type="multipart/form-data",
    )


def test_health_and_index(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "backend": "local"}
    r = client.get("/")
    assert r.status_code == 200
    assert "파일 관리".encode() in r.data


def test_empty_list(client):
    r = client.get("/api/files")
    assert r.status_code == 200
    assert r.get_json() == {"files": [], "count": 0}


def test_upload_list_download_delete(client):
    r = upload(client, "note.txt", b"hello world")
    assert r.status_code == 201, r.data
    rec = r.get_json()["files"][0]
    assert rec["name"] == "note.txt"
    assert rec["size"] == 11
    assert rec["content_type"] == "text/plain"

    r = client.get("/api/files")
    assert r.get_json()["count"] == 1

    r = client.get(f"/api/files/{rec['id']}")
    assert r.status_code == 200
    assert r.get_json()["id"] == rec["id"]

    r = client.get(f"/api/files/{rec['id']}/download")
    assert r.status_code == 200
    assert r.data == b"hello world"
    assert "attachment" in r.headers["Content-Disposition"]
    assert "note.txt" in r.headers["Content-Disposition"]

    r = client.get(f"/api/files/{rec['id']}/preview")
    assert r.status_code == 200
    assert "attachment" not in r.headers.get("Content-Disposition", "")

    r = client.get("/api/stats")
    assert r.get_json()["count"] == 1
    assert r.get_json()["total_size"] == 11

    r = client.delete(f"/api/files/{rec['id']}")
    assert r.status_code == 200
    assert client.get("/api/files").get_json()["count"] == 0
    assert client.get(f"/api/files/{rec['id']}").status_code == 404
    assert client.delete(f"/api/files/{rec['id']}").status_code == 404


def test_multi_upload_and_search(client):
    r = client.post(
        "/api/files",
        data={
            "file": [
                (io.BytesIO(b"a"), "report.pdf", "application/pdf"),
                (io.BytesIO(b"bb"), "photo.png", "image/png"),
                (io.BytesIO(b"ccc"), "Report-final.docx", "application/octet-stream"),
            ]
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert r.get_json()["count"] == 3

    r = client.get("/api/files?q=report")
    names = sorted(f["name"] for f in r.get_json()["files"])
    assert names == ["Report-final.docx", "report.pdf"]

    r = client.get("/api/files?q=nothing")
    assert r.get_json()["count"] == 0


def test_rename(client):
    rec = upload(client, "old.txt").get_json()["files"][0]
    r = client.patch(f"/api/files/{rec['id']}", json={"name": "new.md"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "new.md"
    assert r.get_json()["content_type"] == "text/markdown"

    r = client.patch(f"/api/files/{rec['id']}", json={"name": ""})
    assert r.status_code == 400
    r = client.patch("/api/files/doesnotexist", json={"name": "x"})
    assert r.status_code == 404


def test_korean_filename_kept(client):
    rec = upload(client, "보고서.txt").get_json()["files"][0]
    assert rec["name"] == "보고서.txt"
    r = client.get(f"/api/files/{rec['id']}/download")
    assert r.status_code == 200


def test_path_traversal_stripped(client):
    rec = upload(client, "../../evil.txt").get_json()["files"][0]
    assert rec["name"] == "evil.txt"
    rec = upload(client, "C:\\Windows\\sys.txt").get_json()["files"][0]
    assert rec["name"] == "sys.txt"


def test_bad_uploads(client):
    r = client.post("/api/files", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    r = upload(client, "empty.txt", b"")
    assert r.status_code == 400


def test_too_large(client):
    import app as app_module

    big = b"x" * (app_module.MAX_UPLOAD_BYTES + 1)
    r = upload(client, "big.bin", big, "application/octet-stream")
    assert r.status_code == 413
    assert "too large" in r.get_json()["error"]


def test_404_json_for_api(client):
    r = client.get("/api/files/nope/download")
    assert r.status_code == 404
    assert r.get_json()["error"]


def test_preview_blocks_active_content(client):
    for name, ctype in [("page.html", "text/html"), ("icon.svg", "image/svg+xml"), ("x.xml", "text/xml")]:
        rec = upload(client, name, b"<script>alert(1)</script>", ctype).get_json()["files"][0]
        r = client.get(f"/api/files/{rec['id']}/preview")
        assert r.status_code == 200
        assert r.mimetype == "application/octet-stream"
        assert "attachment" in r.headers["Content-Disposition"]
        assert "sandbox" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_preview_extension_overrides_spoofed_type(client):
    rec = upload(client, "evil.html", b"<b>x</b>", "text/plain").get_json()["files"][0]
    r = client.get(f"/api/files/{rec['id']}/preview")
    assert r.mimetype == "application/octet-stream"
    assert "attachment" in r.headers["Content-Disposition"]


def test_preview_safe_types_stay_inline_with_headers(client):
    rec = upload(client, "pic.png", b"\x89PNG\r\n", "image/png").get_json()["files"][0]
    r = client.get(f"/api/files/{rec['id']}/preview")
    assert r.mimetype == "image/png"
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    assert r.headers["Content-Security-Policy"].startswith("sandbox;")
    rec = upload(client, "doc.pdf", b"%PDF-1.4", "application/pdf").get_json()["files"][0]
    r = client.get(f"/api/files/{rec['id']}/preview")
    assert r.mimetype == "application/pdf"
    assert "sandbox" not in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
