import json
import os
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def upload_video(video_path, metadata_path):
    token_data = json.loads(os.environ["YOUTUBE_TOKEN"])

    credentials = Credentials.from_authorized_user_info(
        token_data,
        scopes=SCOPES,
    )

    youtube = build("youtube", "v3", credentials=credentials)

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    body = {
        "snippet": {
            "title": metadata["youtube_title"],
            "description": metadata["youtube_description"],
            "categoryId": "28",
        },
        "status": {
            "privacyStatus": "private",
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

    while response is None:
        status, response = request.next_chunk()

        if status:
            print(f"YouTube upload: {status.progress() * 100:.1f}%")

    print(f"YouTube upload successful: https://youtu.be/{response['id']}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python youtube_upload.py VIDEO_PATH METADATA_PATH")
        sys.exit(1)

    upload_video(sys.argv[1], sys.argv[2])
