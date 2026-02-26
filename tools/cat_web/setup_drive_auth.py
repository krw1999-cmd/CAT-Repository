"""Run once to authorize Google Drive access. Creates token.json."""
from google_auth_oauthlib.flow import InstalledAppFlow
import pathlib

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
BASE = pathlib.Path(__file__).parent

flow = InstalledAppFlow.from_client_secrets_file(str(BASE / "credentials.json"), SCOPES)
creds = flow.run_local_server(port=0)
(BASE / "token.json").write_text(creds.to_json())
print("✓ token.json saved — Drive API is ready.")
