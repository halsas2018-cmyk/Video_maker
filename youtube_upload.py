import json
import os
import sys
import traceback

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def upload_video(video_path, metadata_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    token_json = os.environ.get("YOUTUBE_TOKEN")
    if not token_json:
        raise RuntimeError("YOUTUBE_TOKEN environment variable not set")

    try:
        token_data = json.loads(token_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"YOUTUBE_TOKEN is not valid JSON: {e}")

    credentials = Credentials.from_authorized_user_info(token_data, scopes=SCOPES)

    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
        print("  [youtube] Refreshed access token")

    youtube = build("youtube", "v3", credentials=credentials)

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    # Validate required fields
    if "youtube_title" not in metadata:
        raise ValueError("Metadata missing 'youtube_title'")
    if "youtube_description" not in metadata:
        raise ValueError("Metadata missing 'youtube_description'")

    body = {
        "snippet": {
            "title": metadata["youtube_title"][:100],  # YouTube max 100 chars
            "description": metadata["youtube_description"][:5000],  # YouTube max 5000 chars
            "categoryId": "28",  # Science & Technology
            "tags": metadata.get("tags", ["AI", "tech", "news"]),
        },
        "status": {
            "privacyStatus": metadata.get("privacy_status", "private"),
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  [youtube] Upload: {status.progress() * 100:.1f}%")

        video_id = response.get("id")
        print(f"  [youtube] ✅ Upload successful: https://youtu.be/{video_id}")
        return video_id

    except HttpError as e:
        error_details = e.error_details if hasattr(e, 'error_details') else str(e)
        raise RuntimeError(f"YouTube API error: {error_details}") from e


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python youtube_upload.py VIDEO_PATH METADATA_PATH", file=sys.stderr)
        sys.exit(1)

    try:
        upload_video(sys.argv[1], sys.argv[2])
    except Exception as e:
        print(f"  [youtube] ❌ Upload failed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
