import os
import sys
import time
import shutil
import tarfile
import zipfile
import tempfile
import subprocess
import urllib.request
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import yt_dlp

# --- Auto-Download FFmpeg Setup with Verbose Logging ---
def ensure_ffmpeg():
    print("[BOOT] Checking system dependencies for FFmpeg and FFprobe...", flush=True)
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        print("[BOOT] -> FFmpeg/FFprobe are already globally accessible via system PATH.", flush=True)
        return

    bin_dir = os.path.abspath("bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    if bin_dir not in os.environ["PATH"]:
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
        print(f"[BOOT] -> Injected local bin directory into environment PATH: {bin_dir}", flush=True)

    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if os.path.exists(os.path.join(bin_dir, ffmpeg_exe)):
        print("[BOOT] -> FFmpeg binaries already cached in local bin folder.", flush=True)
        return

    print("[BOOT] ⚠️ FFmpeg not found! Initiating automated platform-specific download...", flush=True)
    try:
        if sys.platform.startswith("linux"):
            url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
            tar_path = os.path.join(bin_dir, "ffmpeg.tar.xz")
            print(f"[DOWNLOAD] Fetching Linux static binaries from: {url}", flush=True)
            urllib.request.urlretrieve(url, tar_path)
            
            print("[EXTRACT] Unpacking tar.xz archive...", flush=True)
            with tarfile.open(tar_path, "r:xz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("/ffmpeg") or member.name.endswith("/ffprobe"):
                        member.name = os.path.basename(member.name)
                        tar.extract(member, path=bin_dir)
            
            os.chmod(os.path.join(bin_dir, "ffmpeg"), 0o755)
            os.chmod(os.path.join(bin_dir, "ffprobe"), 0o755)
            os.remove(tar_path)
            print("[BOOT] -> FFmpeg successfully compiled and configured for Linux.", flush=True)
            
        elif sys.platform == "win32":
            url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            zip_path = os.path.join(bin_dir, "ffmpeg.zip")
            print(f"[DOWNLOAD] Fetching Windows binaries from: {url}", flush=True)
            urllib.request.urlretrieve(url, zip_path)
            
            print("[EXTRACT] Extracting executable files...", flush=True)
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for member in zip_ref.namelist():
                    if member.endswith("ffmpeg.exe") or member.endswith("ffprobe.exe"):
                        filename = os.path.basename(member)
                        with open(os.path.join(bin_dir, filename), "wb") as f_out:
                            f_out.write(zip_ref.read(member))
            os.remove(zip_path)
            print("[BOOT] -> FFmpeg successfully configured for Windows.", flush=True)
            
        elif sys.platform == "darwin":
            url_ffmpeg = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
            url_ffprobe = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
            
            for name, url in [("ffmpeg", url_ffmpeg), ("ffprobe", url_ffprobe)]:
                zip_path = os.path.join(bin_dir, f"{name}.zip")
                print(f"[DOWNLOAD] Fetching macOS {name} binary from: {url}", flush=True)
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(bin_dir)
                os.remove(zip_path)
                
            os.chmod(os.path.join(bin_dir, "ffmpeg"), 0o755)
            os.chmod(os.path.join(bin_dir, "ffprobe"), 0o755)
            print("[BOOT] -> FFmpeg successfully configured for macOS.", flush=True)
            
    except Exception as e:
        print(f"[ERROR] CRITICAL: Auto-download failed: {e}. Transcoding features may break.", flush=True)

# Run dependency assertion before launching framework
ensure_ffmpeg()


app = FastAPI()

DOWNLOAD_DIR = "cache"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_file(path: str):
    """Safely removes a file from disk after it has been sent to the user."""
    print(f"[CLEANUP] Background worker triggered. Erasing temporary file: {path}", flush=True)
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[CLEANUP] Successfully deleted: {path}", flush=True)
        else:
            print(f"[CLEANUP] Warning: File {path} was already gone.", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed executing cleanup on file {path}: {e}", flush=True)


def download_audio(video_id: str) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")

    print(f"[YT-DLP] Initializing core engine for download. Target URL: {url}", flush=True)
    ydl_opts = {
        "format": "140",
        "outtmpl": "%(title)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "writethumbnail": True,
        "embedmetadata": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            },
            {
                "key": "EmbedThumbnail",
                "already_have_thumbnail": False,
            },
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "png",
                "when": "before_dl",
            },
        ],
        "postprocessor_args": {
            "ffmpeg": ["-vf", "crop=ih:ih"],
        },
    }
    
    start_time = time.time()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    elapsed = time.time() - start_time
    print(f"[YT-DLP] Processing completed in {elapsed:.2f}s. Locating output file structure...", flush=True)

    for ext in ["mp3", "m4a", "webm"]:
        path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
        if os.path.exists(path):
            print(f"[YT-DLP] Validated output file match found: {path}", flush=True)
            return path

    raise RuntimeError("Target output file missing post-extraction.")


@app.get("/audio/{video_id}")
def get_audio(video_id: str):
    print(f"[REQUEST] GET /audio/{video_id}", flush=True)
    try:
        # Check cache
        print(f"[PROCESS] Scanning file cache for existing entry matching ID: {video_id}...", flush=True)
        for ext in ["mp3", "m4a", "webm"]:
            cached = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
            if os.path.exists(cached):
                print(f"[SUCCESS] Cache Hit! Instantly serving local file: {cached}", flush=True)
                return FileResponse(cached, media_type="audio/mpeg")

        print(f"[PROCESS] Cache Miss. Forwarding execution request to download pipeline.", flush=True)
        file_path = download_audio(video_id)
        
        print(f"[SUCCESS] Streaming down conversion back to client: {file_path}", flush=True)
        return FileResponse(file_path, media_type="audio/mpeg")

    except Exception as e:
        print(f"[ERROR] Exception occurred processing /audio/{video_id}: {str(e)}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/artist")
def get_artist(url: str, background_tasks: BackgroundTasks):
    print(f"[REQUEST] GET /artist | URL Target: {url}", flush=True)
    if not url:
        print("[ERROR] Rejected request: Missing mandatory 'url' query parameter.", flush=True)
        raise HTTPException(status_code=400, detail="Missing mandatory 'url' parameter.")

    # Isolated tracking session
    temp_dir = tempfile.mkdtemp()
    print(f"[PROCESS] Allocated dedicated workspace path: {temp_dir}", flush=True)
    
    try:
        output_template = os.path.join(temp_dir, "{artist}", "{album}", "{title}.{output-ext}")
        
        cmd = [
            "spotdl",
            "download",
            url,
            "--output", output_template,
            "--format", "mp3"
        ]
        
        print(f"[SPOTDL] Launching spotDL background process execution thread...", flush=True)
        start_time = time.time()
        process = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_time
        
        print(f"[SPOTDL] Process finished in {elapsed:.2f}s with status code: {process.returncode}", flush=True)
        
        if process.returncode != 0:
            print(f"[ERROR] spotDL sub-engine thrown internal failure:\n{process.stderr}", flush=True)
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"spotDL execution failure: {process.stderr}")

        downloaded_items = os.listdir(temp_dir)
        if not downloaded_items:
            print("[ERROR] spotDL terminated cleanly but zero tracks were downloaded. Check your link validity.", flush=True)
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="No track profiles matched this artist payload.")

        print(f"[ZIP] Compiling discovered structural tracks into archive file format...", flush=True)
        zip_base_name = os.path.join(DOWNLOAD_DIR, f"artist_{os.path.basename(temp_dir)}")
        
        archive_path = shutil.make_archive(zip_base_name, "zip", temp_dir)
        archive_size = os.path.getsize(archive_path) / (1024 * 1024)
        print(f"[ZIP] Target archive fully compiled. Final bundle size: {archive_size:.2f} MB", flush=True)
        
        # Immediate workspace wipe
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("[PROCESS] Dismantled raw track temporary folder workspace.", flush=True)
        
        # Enqueue background task cleanup
        background_tasks.add_task(cleanup_file, archive_path)
        print(f"[SUCCESS] Handing bundle {archive_path} off to client response stream.", flush=True)
        
        return FileResponse(
            archive_path, 
            media_type="application/zip", 
            filename="artist_discography.zip"
        )

    except Exception as e:
        print(f"[ERROR] Exception occurred processing /artist: {str(e)}", flush=True)
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ip")
def get_ip():
    print("[REQUEST] GET /ip", flush=True)
    print("[PROCESS] Dispatching external routing look-up handshake to api.ipify.org...", flush=True)
    try:
        req = urllib.request.Request(
            "https://api.ipify.org", 
            headers={"User-Agent": "Mozilla/5.0 (FastAPI Core Server)"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            public_ip = response.read().decode("utf-8").strip()
        print(f"[SUCCESS] Server outbound network translation determined as: {public_ip}", flush=True)
        return {"ip": public_ip}
    except Exception as e:
        print(f"[ERROR] Failed determining outbound translation payload: {str(e)}", flush=True)
        raise HTTPException(status_code=500, detail=f"Inbound node parsing broken: {str(e)}")
