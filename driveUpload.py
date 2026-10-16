import os
from pathlib import Path
from typing import Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


# -------- Config --------
BASE_DIR = Path(__file__).resolve().parent

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "token.json"

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"client_secret.json", "token.json", "credentials.json"}
ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}  # certificate output types


class DriveAuthError(Exception):
    pass


def _escape_drive_q(value: str) -> str:
    """
    Escape a string for Drive 'q' queries where literals are wrapped in single quotes.
    Drive requires escaping backslash and apostrophe inside string literals.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def Authenticate():
    """
    Authenticate the user and return a Google Drive service object.
    Stores OAuth token in TOKEN_FILE to avoid re-consent every run.
    """
    creds = None

    # Load existing token if available
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Refresh expired token if possible
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    # If no valid creds, run OAuth flow
    if not creds or not creds.valid:
        if not CLIENT_SECRETS_FILE.exists():
            raise DriveAuthError(
                "Missing Google OAuth credentials (client_secret.json).\n\n"
                "Create an OAuth Client ID (Desktop) in Google Cloud, download the JSON, "
                "rename it to 'client_secret.json', and place it in the project folder "
                "(same folder as driveUpload.py / main.py)."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)

        # Save token for next time
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    service = build("drive", "v3", credentials=creds)
    print("Authentication successful!")
    return service


def CheckPermissions(service, required_permissions):
    """
    Basic permissions check. Kept as-is from the original approach.
    """
    try:
        permissions = service.permissions().list(fileId="root").execute()
        for permission in permissions.get("permissions", []):
            role = permission.get("role")
            if role in required_permissions:
                return True
        return False
    except HttpError as error:
        print(f"An error occurred: {error}")
        return False


def CreateFolder(service, folder_name: str, parent_id: Optional[str] = None, set_public_reader: bool = False):
    """
    Create (or find) a folder in Google Drive.
    - Uses a safe query (handles apostrophes, backslashes).
    - Optionally sets 'anyone with link can read' permission on that folder.
    """
    try:
        if not CheckPermissions(service, ["owner", "writer"]):
            raise Exception("Insufficient permissions.")

        safe_folder_name = _escape_drive_q(folder_name)
        query = f"name='{safe_folder_name}' and mimeType='application/vnd.google-apps.folder'"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = service.files().list(q=query, fields="files(id)").execute()
        folders = results.get("files", [])

        if folders:
            # Folder already exists
            folder_id = folders[0]["id"]
        else:
            folder_metadata = {
                "name": folder_name,  # real name can include apostrophes, commas, etc.
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                folder_metadata["parents"] = [parent_id]

            folder = service.files().create(body=folder_metadata, fields="id").execute()
            folder_id = folder.get("id")
            print(f"Folder created: {folder_name}")

        if set_public_reader:
            permission = {"type": "anyone", "role": "reader"}
            service.permissions().create(fileId=folder_id, body=permission).execute()
            print("Folder permission set: anyone can read")

        return folder_id

    except HttpError as error:
        print(f"An error occured: {error}")
        return None


def _ensure_drive_subfolders(
    service,
    base_drive_folder_id: str,
    rel_dir: str,
    folder_cache: Dict[str, str],
) -> str:
    """
    Ensure that rel_dir (e.g. 'subfolder,test/inner') exists under base_drive_folder_id.
    Returns the Drive folder ID of rel_dir.
    """
    if rel_dir in ("", ".", None):
        return base_drive_folder_id

    # Normalize to forward slashes
    rel_dir = rel_dir.replace("\\", "/").strip("/")

    if rel_dir in folder_cache:
        return folder_cache[rel_dir]

    parts = rel_dir.split("/")
    cur_parent_id = base_drive_folder_id
    cur_rel = ""

    for part in parts:
        cur_rel = part if cur_rel == "" else f"{cur_rel}/{part}"
        if cur_rel not in folder_cache:
            # subfolders inherit permissions from parent by default,
            # so we don't set public permission for each subfolder
            created_id = CreateFolder(service, part, parent_id=cur_parent_id, set_public_reader=False)
            if not created_id:
                raise Exception(f"Failed to create/find Drive subfolder: {cur_rel}")
            folder_cache[cur_rel] = created_id
        cur_parent_id = folder_cache[cur_rel]

    return folder_cache[rel_dir]


def Upload(file_path: str, parent_folder_name: str):
    """
    Upload a file or folder to Google Drive.
    - Creates (or reuses) a Drive folder named parent_folder_name.
    - Preserves local subfolder structure inside the Drive folder.
    - Skips junk folders/files and only uploads allowed extensions.
    - Avoids uploading duplicates by checking if a file with same name already exists in the target Drive folder.
    Returns the Drive folder URL on success.
    """
    service = Authenticate()

    try:
        if not CheckPermissions(service, ["owner", "writer"]):
            raise Exception("Insufficient permissions.")

        # Create/find top-level destination folder and make it public-readable
        parent_folder_id = CreateFolder(service, parent_folder_name, parent_id=None, set_public_reader=True)
        if not parent_folder_id:
            raise Exception("Failed to create/find target folder in Drive.")

        folder_url = f"https://drive.google.com/drive/folders/{parent_folder_id}"
        print(f"Drive folder link: {folder_url}")

        # Upload a folder (preserve structure)
        if os.path.isdir(file_path):
            base = Path(file_path)
            folder_cache: Dict[str, str] = {"." : parent_folder_id}

            for root, dirs, files in os.walk(file_path):
                # prevent walking into unwanted folders
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

                root_path = Path(root)
                rel_dir = os.path.relpath(root_path, base)
                rel_dir = "." if rel_dir == "." else rel_dir.replace("\\", "/")

                target_folder_id = _ensure_drive_subfolders(
                    service=service,
                    base_drive_folder_id=parent_folder_id,
                    rel_dir=rel_dir if rel_dir != "." else "",
                    folder_cache=folder_cache,
                )

                for filename in files:
                    if filename in SKIP_FILES:
                        continue

                    ext = Path(filename).suffix.lower()
                    if ext not in ALLOWED_EXTS:
                        continue

                    local_path = str(root_path / filename)

                    # duplicate check in the correct Drive folder
                    safe_name = _escape_drive_q(filename)
                    query = f"name='{safe_name}' and '{target_folder_id}' in parents"
                    results = service.files().list(q=query, fields="files(id)").execute()
                    if results.get("files", []):
                        print(f"File '{filename}' already exists. Skipping.")
                        continue

                    file_metadata = {"name": filename, "parents": [target_folder_id]}
                    media = MediaFileUpload(local_path, resumable=True)

                    uploaded = service.files().create(body=file_metadata, media_body=media).execute()
                    print(f"Uploaded {uploaded['name']}")

        # Upload a single file
        else:
            filename = os.path.basename(file_path)
            if filename in SKIP_FILES:
                raise Exception("Refusing to upload a secret/token file.")

            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXTS:
                raise Exception(f"File type not allowed: {ext}")

            safe_name = _escape_drive_q(filename)
            query = f"name='{safe_name}' and '{parent_folder_id}' in parents"
            results = service.files().list(q=query, fields="files(id)").execute()
            if results.get("files", []):
                print(f"File '{filename}' already exists. Skipping.")
            else:
                file_metadata = {"name": filename, "parents": [parent_folder_id]}
                media = MediaFileUpload(file_path, resumable=True)

                uploaded = service.files().create(body=file_metadata, media_body=media).execute()
                print(f"Uploaded {uploaded['name']}")

        print("Process completed successfully!")
        return folder_url

    except HttpError as error:
        print(f"An error occured: {error}")
        return None