from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_FILE = "/root/video_maker/client_secret.json"
TOKEN_FILE = "/root/video_maker/token.json"

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)

creds = flow.run_local_server(
    host="127.0.0.1",
    port=8090,
    open_browser=False,
    access_type="offline",
    prompt="consent",
)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print(f"\nYouTube authorization successful.")
print(f"Token saved to: {TOKEN_FILE}")
