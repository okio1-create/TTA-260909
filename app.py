"""Flask file manager.

Routes
------
GET  /                         HTML UI
GET  /api/files?q=             list files (JSON)
POST /api/files                upload one or more files (multipart "file")
GET  /api/files/<id>           file metadata
GET  /api/files/<id>/download  download (attachment)
GET  /api/files/<id>/preview   inline view (images / text / pdf)
PATCH /api/files/<id>          rename  {"name": "..."}
DELETE /api/files/<id>         delete
GET  /api/stats                count + total bytes
GET  /health                   health check
"""

from __future__ import annotations

import io
import os

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from storage import build_storage

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 4 * 1024 * 1024))  # Vercel limit ~4.5 MB

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
storage = build_storage()


def _clean_name(name: str) -> str:
    """Keep the user's filename readable but strip path components."""
    name = os.path.basename(name.replace("\\", "/")).strip()
    if not name or name in {".", ".."}:
        return ""
    # secure_filename removes non-ascii; keep original when it still has content
    safe = secure_filename(name)
    return name if safe or not name.isascii() else safe


def _record_or_404(file_id: str):
    rec = storage.get(file_id)
    if rec is None:
        abort(404, description="file not found")
    return rec


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return render_template(
        "index.html",
        backend=storage.backend_name,
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "backend": storage.backend_name})


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/files")
def list_files():
    q = request.args.get("q", "").strip()
    files = storage.list_files(q)
    return jsonify({"files": [f.to_dict() for f in files], "count": len(files)})


@app.post("/api/files")
def upload_files():
    uploads = request.files.getlist("file")
    uploads = [u for u in uploads if u and u.filename]
    if not uploads:
        return jsonify({"error": "no file provided (use form field 'file')"}), 400

    saved = []
    for up in uploads:
        name = _clean_name(up.filename)
        if not name:
            return jsonify({"error": f"invalid filename: {up.filename!r}"}), 400
        data = up.read()
        if not data:
            return jsonify({"error": f"empty file: {name}"}), 400
        rec = storage.save(name, data, up.mimetype)
        saved.append(rec.to_dict())
    return jsonify({"files": saved, "count": len(saved)}), 201


@app.get("/api/files/<file_id>")
def file_info(file_id: str):
    return jsonify(_record_or_404(file_id).to_dict())


def _send(file_id: str, as_attachment: bool):
    rec = _record_or_404(file_id)
    data = storage.read(file_id)
    if data is None:
        abort(404, description="file content missing")
    return send_file(
        io.BytesIO(data),
        mimetype=rec.content_type,
        as_attachment=as_attachment,
        download_name=rec.name,
        max_age=0,
    )


@app.get("/api/files/<file_id>/download")
def download_file(file_id: str):
    return _send(file_id, as_attachment=True)


@app.get("/api/files/<file_id>/preview")
def preview_file(file_id: str):
    return _send(file_id, as_attachment=False)


@app.patch("/api/files/<file_id>")
def rename_file(file_id: str):
    body = request.get_json(silent=True) or {}
    new_name = _clean_name(str(body.get("name", "")))
    if not new_name:
        return jsonify({"error": "name is required"}), 400
    rec = storage.rename(file_id, new_name)
    if rec is None:
        abort(404, description="file not found")
    return jsonify(rec.to_dict())


@app.delete("/api/files/<file_id>")
def delete_file(file_id: str):
    if not storage.delete(file_id):
        abort(404, description="file not found")
    return jsonify({"deleted": file_id})


@app.get("/api/stats")
def stats():
    s = storage.stats()
    s["backend"] = storage.backend_name
    return jsonify(s)


# --------------------------------------------------------------------------- #
# Error handlers -> JSON for API, plain text otherwise
# --------------------------------------------------------------------------- #
@app.errorhandler(404)
def not_found(err):
    if request.path.startswith("/api/"):
        return jsonify({"error": getattr(err, "description", "not found")}), 404
    return Response("Not found", status=404)


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": f"file too large (max {MAX_UPLOAD_BYTES} bytes)"}), 413


@app.errorhandler(500)
def server_error(err):
    return jsonify({"error": "internal server error", "detail": str(err)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
