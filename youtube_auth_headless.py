from google_auth_oauthlib.flow import InstalledAppFlow
import json
import os

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_FILE = "/root/video_maker/client_secret.json"
TOKEN_FILE = "/root/video_maker/token.json"

flow = InstalledAppFlow.from_client_secrets_file(
    CLIENT_FILE,
    SCOPES,
)

print("Starting YouTube authorization...")
print("A browser may open automatically.")
print("If it does not, copy the displayed URL into your browser.")

creds = flow.run_local_server(
    host="localhost",
    port=0,
    authorization_prompt_message="Open this URL to authorize: {url}",
    success_message="YouTube authorization successful. You may close this tab.",
    open_browser=False,
)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

os.chmod(TOKEN_FILE, 0o600)

print("\nYouTube authorization successful.")
print(f"Token saved to: {TOKEN_FILE}")

token_data = json.loads(creds.to_json())
print("\nTo use this token for automated uploads, run:")
print(
    "export YOUTUBE_TOKEN="
    + "'" + json.dumps(token_data).replace("'", "'\\''") + "'"
)
