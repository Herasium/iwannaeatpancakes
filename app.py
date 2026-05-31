import os
import sys
import shutil
import tarfile
import zipfile
import tempfile
import subprocess
import urllib.request
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import yt_dlp

# --- Auto-Download FFmpeg Setup ---
def ensure_ffmpeg():
    """
    Checks if FFmpeg is available globally. If not, auto-downloads 
    the proper static binary for Linux, Windows, or macOS, and patches PATH.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        print("FFmpeg is already accessible via system PATH.")
        return

    bin_dir = os.path.abspath("bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    # Immediately patch PATH so both subprocesses and modules can read it
    if bin_dir not in os.environ["PATH"]:
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if os.path.exists(os.path.join(bin_dir, ffmpeg_exe)):
        print("FFmpeg already downloaded in local bin folder.")
        return

    print("FFmpeg not found! Initiating automated download of static binaries...")
    try:
        if sys.platform.startswith("linux"):
            url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
            tar_path = os.path.join(bin_dir, "ffmpeg.tar.xz")
            urllib.request.urlretrieve(url, tar_path)
            
            with tarfile.open(tar_path, "r:xz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("/ffmpeg") or member.name.endswith("/ffprobe"):
                        member.name = os.path.basename(member.name)  # Flatten structure
                        tar.extract(member, path=bin_dir)
            
            os.chmod(os.path.join(bin_dir, "ffmpeg"), 0o755)
            os.chmod(os.path.join(bin_dir, "ffprobe"), 0o755)
            os.remove(tar_path)
            print("FFmpeg successfully installed for Linux.")
            
        elif sys.platform == "win32":
            url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            zip_path = os.path.join(bin_dir, "ffmpeg.zip")
            urllib.request.urlretrieve(url, zip_path)
            
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for member in zip_ref.namelist():
                    if member.endswith("ffmpeg.exe") or member.endswith("ffprobe.exe"):
                        filename = os.path.basename(member)
                        with open(os.path.join(bin_dir, filename), "wb") as f_out:
                            f_out.write(zip_ref.read(member))
            os.remove(zip_path)
            print("FFmpeg successfully installed for Windows.")
            
        elif sys.platform == "darwin":
            url_ffmpeg = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
            url_ffprobe = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
            
            for name, url in [("ffmpeg", url_ffmpeg), ("ffprobe", url_ffprobe)]:
                zip_path = os.path.join(bin_dir, f"{name}.zip")
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(bin_dir)
                os.remove(zip_path)
                
            os.chmod(os.path.join(bin_dir, "ffmpeg"), 0o755)
            os.chmod(os.path.join(bin_dir, "ffprobe"), 0o755)
            print("FFmpeg successfully installed for macOS.")
            
    except Exception as e:
        print(f"CRITICAL WARNING: Auto-download failed: {e}. App may fail to encode audio.")

# Execute the binary check before booting the API
ensure_ffmpeg()


app = FastAPI()

DOWNLOAD_DIR = "cache"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_file(path: str):
    """Safely removes a file from disk after it has been sent to the user."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Error executing file cleanup: {e}")


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

    for ext in ["mp3", "m4a", "webm"]:
        path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
        if os.path.exists(path):
            return path

    raise RuntimeError("Download failed")


@app.get("/audio/{video_id}")
def get_audio(video_id: str):
    try:
        for ext in ["mp3", "m4a", "webm"]:
            cached = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
            if os.path.exists(cached):
                return FileResponse(cached, media_type="audio/mpeg")

        file_path = download_audio(video_id)
        return FileResponse(file_path, media_type="audio/mpeg")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/artist")
def get_artist(url: str, background_tasks: BackgroundTasks):
    """
    Downloads an entire Spotify artist discography via spotDL, packages
    the structural tracks into a single zip file, and streams it back.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing mandatory 'url' parameter.")

    # Generate a unique isolated temp directory for this download request
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Layout template maps to clean nested directories automatically
        output_template = os.path.join(temp_dir, "{artist}", "{album}", "{track-number} - {title}.{output-ext}")
        
        cmd = [
            "spotdl",
            "download",
            url,
            "--output", output_template,
            "--format", "mp3"
        ]
        
        # Run process synchronously
        process = subprocess.run(cmd, capture_output=True, text=True)
        
        if process.returncode != 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"spotDL failure: {process.stderr}")

        if not os.listdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="No audio items were downloaded for this URL.")

        # Zip the entire folder structure
        zip_base_name = os.path.join(DOWNLOAD_DIR, f"artist_{os.path.basename(temp_dir)}")
        archive_path = shutil.make_archive(zip_base_name, "zip", temp_dir)
        
        # Drop raw unzipped tracks immediately to save disk space
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Register background hook to erase the zip file from the cache AFTER download completion
        background_tasks.add_task(cleanup_file, archive_path)
        
        return FileResponse(
            archive_path, 
            media_type="application/zip", 
            filename="artist_discography.zip"
        )

    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ip")
def get_ip():
    """
    Pings an outward-facing IP reflection API to return the server's public IP address.
    """
    try:
        req = urllib.request.Request(
            "https://api.ipify.org", 
            headers={"User-Agent": "Mozilla/5.0 (FastAPI Core Server)"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            public_ip = response.read().decode("utf-8").strip()
        return {"ip": public_ip}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch public IP: {str(e)}")
