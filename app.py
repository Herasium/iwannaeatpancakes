import os
import tempfile
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import yt_dlp

app = FastAPI()

DOWNLOAD_DIR = "cache"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def download_audio(video_id: str) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"

    out_template = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # find resulting file
    for ext in ["mp3", "m4a", "webm"]:
        path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
        if os.path.exists(path):
            return path

    raise RuntimeError("Download failed")


@app.get("/audio/{video_id}")
def get_audio(video_id: str):
    try:
        # cache check
        for ext in ["mp3", "m4a", "webm"]:
            cached = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
            if os.path.exists(cached):
                return FileResponse(cached, media_type="audio/mpeg")

        file_path = download_audio(video_id)

        return FileResponse(file_path, media_type="audio/mpeg")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
