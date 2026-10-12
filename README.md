# PongCast Server

The backend REST API and real-time streaming server for PongCast. Built with FastAPI, it manages file uploads, initiates background engine workers, and streams live terminal logs and frame progress via Server-Sent Events (SSE).

**Repository**: [https://github.com/nelson-david/pongbot-server.git](https://github.com/nelson-david/pongbot-server.git)

---

## Features

- **Asynchronous Execution**: Processes video jobs in background thread pools without blocking web requests.
- **Real-Time SSE Streaming**: Emits live progress, ITTF scores, and terminal logs to the frontend.
- **Upload Guard**: Validates video files against a strict 100 MB payload limit (HTTP 413 on excess).
- **Persistent Logging**: Rotating file logging to `server/logs/server.log` (10 MB rotation, 5 backups).
- **Sentry Integration**: Automatic crash reporting when `SENTRY_DSN` is configured.

---

## Setup & Running

### 1. Prerequisites
Ensure the `engine` dependencies and system `ffmpeg` are installed.

### 2. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Variables (Optional)
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base public URL used for generated video URLs |
| `SENTRY_DSN` | *(None)* | Sentry DSN for remote error monitoring |
| `PORT` | `8000` | Port to run Uvicorn on |

### 4. Start the Server
```bash
python3 main.py
```
> Running on: `http://0.0.0.0:8000`  
> Health check: `http://localhost:8000/health`  
> Process endpoint: `POST /api/process`
