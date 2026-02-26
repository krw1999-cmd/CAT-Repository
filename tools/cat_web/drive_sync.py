"""Google Drive API helpers for auto-linking PDFs to transactions/expenses."""
from __future__ import annotations
import json, os, pathlib, re
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
BASE   = pathlib.Path(__file__).parent

def _get_service():
    """Build an authenticated Drive service, refreshing token if needed.

    Credential source priority:
      1. tools/cat_web/token.json  (local dev)
      2. GOOGLE_TOKEN_JSON env var  (Railway / production)
    """
    token_path = BASE / "token.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
    else:
        raw = os.environ.get("GOOGLE_TOKEN_JSON", "")
        if not raw:
            return None
        creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Can't write back to a file in production — env var holds the token

    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _folder_id_from_url(url: str) -> str | None:
    """Extract folder ID from a Google Drive folder URL."""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None

def list_folder_files(folder_url: str) -> list[dict]:
    """Return [{name, id, webViewLink}] for all files in the folder."""
    svc = _get_service()
    if not svc:
        return []
    folder_id = _folder_id_from_url(folder_url)
    if not folder_id:
        return []
    results = svc.files().list(
        q=f"'{folder_id}' in parents and mimeType!='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name,webViewLink)",
        pageSize=200,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return results.get("files", [])

def match_check(files: list[dict], check_number: str) -> str | None:
    """Find a file whose name contains '#{check_number}' (case-insensitive).
    Matches 'check#', 'chech#', or any prefix before '#' to handle typos."""
    if not check_number:
        return None
    pattern = f"#{check_number}".lower()
    for f in files:
        if pattern in f["name"].lower():
            return f["webViewLink"]
    return None

def match_invoice(files: list[dict], invoice_number: str) -> str | None:
    """Find a file whose name contains the invoice number (case-insensitive)."""
    if not invoice_number:
        return None
    needle = invoice_number.lower()
    for f in files:
        if needle in f["name"].lower():
            return f["webViewLink"]
    return None

def drive_available() -> bool:
    return (BASE / "token.json").exists() or bool(os.environ.get("GOOGLE_TOKEN_JSON"))
