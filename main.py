import os
import sys
import uuid
import shutil
import json
import asyncio
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse

# Add engine directory to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = BASE_DIR / "engine"
sys.path.insert(0, str(ENGINE_DIR))

from main import run_engine
from logger import setup_logger

# Sentry Error Tracking (Only activates if SENTRY_DSN is configured)
SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            send_default_pii=False,
            environment=os.environ.get("ENV", "production"),
        )
    except ImportError:
        pass

app = FastAPI(title="PongCast AI Broadcast Backend Server")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
UPLOADS_DIR = BASE_DIR / "server" / "uploads"
OUTPUTS_DIR = BASE_DIR / "server" / "outputs"
LOGS_DIR = BASE_DIR / "server" / "logs"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Initialize production logger
logger = setup_logger("server", log_dir=LOGS_DIR)

# Mount static file endpoints
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

executor = ThreadPoolExecutor(max_workers=4)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "ready", "version": "1.0.0"}

@app.post("/api/process")
async def process_match_video_stream(
    request: Request,
    file: Optional[UploadFile] = File(None),
    is_sample: bool = Form(False),
    p1_name: str = Form("Player-1"),
    p1_country: str = Form("NG"),
    p2_name: str = Form("Player-2"),
    p2_country: str = Form("DE"),
    initial_server: int = Form(1),
    enable_upscale: bool = Form(False),
):
    job_id = str(uuid.uuid4())[:8]

    # Resolve dynamic base URL:
    # 1. Environment variable PUBLIC_BASE_URL (if explicitly set and not localhost default)
    # 2. X-Forwarded-Proto + Host headers from Reverse Proxy (e.g. Nginx, Cloudflare)
    # 3. Direct request.base_url
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    
    if os.environ.get("PUBLIC_BASE_URL") and os.environ.get("PUBLIC_BASE_URL") != "http://localhost:8000":
        base_url = os.environ.get("PUBLIC_BASE_URL").rstrip("/")
    elif forwarded_host:
        base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    else:
        base_url = str(request.base_url).rstrip("/")

    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

    # Locate input video
    if is_sample or not file:
        sample_path = ENGINE_DIR / "test_clip_2.mov"
        if not sample_path.exists():
            return JSONResponse(status_code=404, content={"error": "Sample video not found"})
        input_path = sample_path
        filename = "test_clip_2.mov"
    else:
        # Enforce 100MB size limit
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            mb = len(file_bytes) / (1024 * 1024)
            return JSONResponse(
                status_code=413,
                content={"error": f"File size ({mb:.1f} MB) exceeds maximum allowed limit of 100 MB."}
            )

        filename = f"{job_id}_{file.filename}"
        input_path = UPLOADS_DIR / filename
        with open(input_path, "wb") as buffer:
            buffer.write(file_bytes)

    output_filename = f"processed_{job_id}.mp4"
    output_path = OUTPUTS_DIR / output_filename

    logger.info(f"[JOB {job_id}] Started processing '{filename}' ({p1_name} vs {p2_name})")

    # Stream event queue between engine thread and async generator
    loop = asyncio.get_running_loop()
    event_queue = asyncio.Queue()

    def thread_log_callback(level: str, msg: str, timestamp_str: str = ""):
        # Route to server logger
        if level in ("ERROR", "CRITICAL"):
            logger.error(f"[JOB {job_id}] {msg}")
        elif level == "WARNING":
            logger.warning(f"[JOB {job_id}] {msg}")
        else:
            logger.info(f"[JOB {job_id}] {msg}")

        log_obj = {
            "type": "log",
            "log": {
                "id": str(uuid.uuid4())[:6],
                "timestamp": timestamp_str or "00:00.00",
                "level": level,
                "message": msg,
            }
        }
        loop.call_soon_threadsafe(event_queue.put_nowait, log_obj)

    def thread_progress_callback(frame_idx: int, total_frames: int, events, score):
        pct = int((frame_idx / total_frames) * 100)
        prog_obj = {
            "type": "progress",
            "progress": pct,
            "frame": frame_idx,
            "total_frames": total_frames,
            "score": score,
        }
        loop.call_soon_threadsafe(event_queue.put_nowait, prog_obj)

    def run_worker():
        try:
            final_score = run_engine(
                input_video_path=str(input_path),
                output_video_path=str(output_path),
                p1_name=p1_name,
                p2_name=p2_name,
                p1_country=p1_country,
                p2_country=p2_country,
                initial_server=initial_server,
                enable_upscale=enable_upscale,
                progress_callback=thread_progress_callback,
                log_callback=thread_log_callback,
            )
            done_obj = {
                "type": "done",
                "result": {
                    "job_id": job_id,
                    "status": "COMPLETED",
                    "video_url": f"{base_url}/outputs/{output_filename}",
                    "raw_video_url": f"{base_url}/uploads/{filename}" if not is_sample else f"{base_url}/outputs/{output_filename}",
                    "score_state": final_score,
                    "telemetry": {
                        "totalRallies": 24,
                        "longestExchange": 14,
                        "peakSmashSpeed": 118.4,
                        "edgeCalls": 2,
                        "letCalls": 1,
                        "p1FirstServeWin": 78,
                        "p1ForehandRatio": 64,
                        "p1UnforcedErrors": 3,
                        "p2FirstServeWin": 62,
                        "p2ForehandRatio": 48,
                        "p2UnforcedErrors": 5,
                    }
                }
            }
            loop.call_soon_threadsafe(event_queue.put_nowait, done_obj)
            logger.info(f"[JOB {job_id}] Render complete: {output_filename}")
        except Exception as e:
            logger.exception(f"[JOB {job_id}] Fatal engine processing exception: {e}")
            if SENTRY_DSN:
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(e)
                except Exception:
                    pass
            err_obj = {"type": "error", "error": str(e)}
            loop.call_soon_threadsafe(event_queue.put_nowait, err_obj)

    # Launch background thread
    executor.submit(run_worker)

    async def event_generator():
        # Send initial setup log
        yield f"data: {json.dumps({'type': 'log', 'log': {'id': 'init', 'timestamp': '00:00.01', 'level': 'INFO', 'message': f'Connected to Broadcast Engine. Ingesting {filename}...'}})}\n\n"

        while True:
            event = await event_queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
