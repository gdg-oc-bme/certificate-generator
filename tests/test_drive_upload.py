from types import SimpleNamespace

import pytest

import driveUpload as du
from driveUpload import (
    DriveAuthError,
    _escape_drive_q,
    CheckPermissions,
    CreateFolder,
    _ensure_drive_subfolders,
    Upload,
)
from googleapiclient.errors import HttpError


class DummyRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.payload


class DummyFilesAPI:
    def __init__(self, list_results=None):
        self.list_results = list_results or {}
        self.list_queries = []
        self.create_calls = []

    def list(self, q=None, fields=None):
        self.list_queries.append({"q": q, "fields": fields})
        payload = {"files": self.list_results.get(q, [])}
        return DummyRequest(payload=payload)

    def create(self, body=None, fields=None, media_body=None):
        self.create_calls.append(
            {"body": body, "fields": fields, "media_body": media_body}
        )
        payload = {"id": f"created_{len(self.create_calls)}", "name": body.get("name", "")}
        return DummyRequest(payload=payload)


class DummyPermissionsAPI:
    def __init__(self, roles=None, list_error=None):
        self.roles = roles or []
        self.list_error = list_error
        self.create_calls = []

    def list(self, fileId=None):
        if self.list_error:
            return DummyRequest(error=self.list_error)
        payload = {"permissions": [{"role": role} for role in self.roles]}
        return DummyRequest(payload=payload)

    def create(self, fileId=None, body=None):
        self.create_calls.append({"fileId": fileId, "body": body})
        return DummyRequest(payload={"id": "perm_1"})


class DummyService:
    def __init__(self, list_results=None, roles=None, list_error=None):
        self.files_api = DummyFilesAPI(list_results=list_results)
        self.permissions_api = DummyPermissionsAPI(roles=roles, list_error=list_error)

    def files(self):
        return self.files_api

    def permissions(self):
        return self.permissions_api


class DummyMediaFileUpload:
    def __init__(self, path, resumable=True):
        self.path = path
        self.resumable = resumable


def make_http_error(status=500):
    resp = SimpleNamespace(status=status, reason="boom")
    return HttpError(resp=resp, content=b"boom")


def test_escape_drive_q_escapes_apostrophes_and_backslashes():
    assert _escape_drive_q("O'Brien\\Docs") == "O\\'Brien\\\\Docs"


def test_authenticate_raises_when_client_secret_missing(tmp_path, monkeypatch):
    missing_secret = tmp_path / "client_secret.json"
    token_file = tmp_path / "token.json"

    monkeypatch.setattr(du, "CLIENT_SECRETS_FILE", missing_secret)
    monkeypatch.setattr(du, "TOKEN_FILE", token_file)

    with pytest.raises(DriveAuthError) as exc:
        du.Authenticate()

    assert "client_secret.json" in str(exc.value)


def test_check_permissions_returns_true_for_writer_role():
    service = DummyService(roles=["reader", "writer"])
    assert CheckPermissions(service, ["owner", "writer"]) is True


def test_check_permissions_returns_false_on_http_error():
    service = DummyService(list_error=make_http_error())
    assert CheckPermissions(service, ["owner", "writer"]) is False


def test_create_folder_returns_existing_folder_and_sets_public_permission(monkeypatch):
    query = "name='Team Folder' and mimeType='application/vnd.google-apps.folder'"
    service = DummyService(
        list_results={query: [{"id": "folder_123"}]},
        roles=["owner"],
    )

    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)

    folder_id = CreateFolder(service, "Team Folder", set_public_reader=True)

    assert folder_id == "folder_123"
    assert service.files_api.create_calls == []
    assert service.permissions_api.create_calls == [
        {"fileId": "folder_123", "body": {"type": "anyone", "role": "reader"}}
    ]


def test_create_folder_creates_new_folder_under_parent(monkeypatch):
    query = (
        "name='Sub Folder' and mimeType='application/vnd.google-apps.folder' "
        "and 'parent_1' in parents"
    )
    service = DummyService(list_results={query: []}, roles=["writer"])

    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)

    folder_id = CreateFolder(service, "Sub Folder", parent_id="parent_1", set_public_reader=False)

    assert folder_id == "created_1"
    assert service.files_api.create_calls[0]["body"] == {
        "name": "Sub Folder",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["parent_1"],
    }


def test_ensure_drive_subfolders_creates_missing_nested_folders(monkeypatch):
    created = []

    def fake_create_folder(service, folder_name, parent_id=None, set_public_reader=False):
        created.append((folder_name, parent_id, set_public_reader))
        return f"id_{folder_name}"

    monkeypatch.setattr(du, "CreateFolder", fake_create_folder)

    folder_cache = {}
    result = _ensure_drive_subfolders(object(), "base_id", "sub/inner", folder_cache)

    assert result == "id_inner"
    assert created == [
        ("sub", "base_id", False),
        ("inner", "id_sub", False),
    ]
    assert folder_cache == {"sub": "id_sub", "sub/inner": "id_inner"}


def test_upload_single_file_skips_duplicate_and_returns_folder_url(tmp_path, monkeypatch):
    file_path = tmp_path / "John Doe.pdf"
    file_path.write_text("pdf", encoding="utf-8")

    query = "name='John Doe.pdf' and 'folder_1' in parents"
    service = DummyService(list_results={query: [{"id": "existing_file"}]})

    monkeypatch.setattr(du, "Authenticate", lambda: service)
    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)
    monkeypatch.setattr(du, "CreateFolder", lambda *args, **kwargs: "folder_1")
    monkeypatch.setattr(du, "MediaFileUpload", DummyMediaFileUpload)

    result = Upload(str(file_path), "Certificates")

    assert result == "https://drive.google.com/drive/folders/folder_1"
    assert service.files_api.create_calls == []


def test_upload_single_file_creates_file_when_not_duplicate(tmp_path, monkeypatch):
    file_path = tmp_path / "John Doe.pdf"
    file_path.write_text("pdf", encoding="utf-8")

    query = "name='John Doe.pdf' and 'folder_1' in parents"
    service = DummyService(list_results={query: []})

    monkeypatch.setattr(du, "Authenticate", lambda: service)
    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)
    monkeypatch.setattr(du, "CreateFolder", lambda *args, **kwargs: "folder_1")
    monkeypatch.setattr(du, "MediaFileUpload", DummyMediaFileUpload)

    result = Upload(str(file_path), "Certificates")

    assert result == "https://drive.google.com/drive/folders/folder_1"
    assert len(service.files_api.create_calls) == 1
    create_call = service.files_api.create_calls[0]
    assert create_call["body"] == {"name": "John Doe.pdf", "parents": ["folder_1"]}
    assert isinstance(create_call["media_body"], DummyMediaFileUpload)
    assert create_call["media_body"].path == str(file_path)


def test_upload_single_file_rejects_secret_and_disallowed_extensions(tmp_path, monkeypatch):
    secret_file = tmp_path / "token.json"
    secret_file.write_text("secret", encoding="utf-8")

    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(du, "Authenticate", lambda: DummyService())
    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)
    monkeypatch.setattr(du, "CreateFolder", lambda *args, **kwargs: "folder_1")

    with pytest.raises(Exception) as secret_exc:
        Upload(str(secret_file), "Certificates")
    assert "secret/token file" in str(secret_exc.value)

    with pytest.raises(Exception) as ext_exc:
        Upload(str(txt_file), "Certificates")
    assert "File type not allowed" in str(ext_exc.value)


def test_upload_folder_skips_junk_and_preserves_subfolders(tmp_path, monkeypatch):
    base = tmp_path / "certs"
    base.mkdir()
    (base / "John Doe.pdf").write_text("pdf", encoding="utf-8")
    (base / "notes.txt").write_text("ignore", encoding="utf-8")
    (base / "client_secret.json").write_text("ignore", encoding="utf-8")

    inner = base / "subfolder"
    inner.mkdir()
    (inner / "Jane's Certificate.jpg").write_text("jpg", encoding="utf-8")

    hidden = base / ".git"
    hidden.mkdir()
    (hidden / "secret.pdf").write_text("ignore", encoding="utf-8")

    service = DummyService(list_results={})
    created_subfolders = []

    def fake_ensure_drive_subfolders(service, base_drive_folder_id, rel_dir, folder_cache):
        if rel_dir in ("", ".", None):
            return base_drive_folder_id
        folder_cache[rel_dir] = f"id_{rel_dir.replace('/', '_')}"
        created_subfolders.append(rel_dir)
        return folder_cache[rel_dir]

    monkeypatch.setattr(du, "Authenticate", lambda: service)
    monkeypatch.setattr(du, "CheckPermissions", lambda *args, **kwargs: True)
    monkeypatch.setattr(du, "CreateFolder", lambda *args, **kwargs: "folder_1")
    monkeypatch.setattr(du, "_ensure_drive_subfolders", fake_ensure_drive_subfolders)
    monkeypatch.setattr(du, "MediaFileUpload", DummyMediaFileUpload)

    result = Upload(str(base), "Certificates")

    assert result == "https://drive.google.com/drive/folders/folder_1"
    assert created_subfolders == ["subfolder"]
    uploaded_names = [call["body"]["name"] for call in service.files_api.create_calls]
    assert uploaded_names == ["John Doe.pdf", "Jane's Certificate.jpg"]
    parent_ids = [call["body"]["parents"][0] for call in service.files_api.create_calls]
    assert parent_ids == ["folder_1", "id_subfolder"]

    queries = [item["q"] for item in service.files_api.list_queries]
    assert "name='John Doe.pdf' and 'folder_1' in parents" in queries
    assert "name='Jane\\'s Certificate.jpg' and 'id_subfolder' in parents" in queries