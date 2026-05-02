"""
SwingIQ v3 — Backend API
FastAPI: Claude proxy + auth + session persistence + video upload & analysis
"""
import os, uuid, asyncio, json
from typing import AsyncGenerator, Optional
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, get_db, create_session, get_user_sessions, save_chat_message, get_chat_history, SwingSession
from auth import RegisterRequest, LoginRequest, TokenResponse, UserOut, register_handler, login_handler, get_current_user, get_optional_user, User

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="SwingIQ API", version="3.1.0")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/swingiq_uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store {job_id: {status, progress, result, error}}
_jobs: dict[str, dict] = {}

@app.on_event("startup")
def startup():
    init_db()
    print("✓ DB ready  |  upload dir:", UPLOAD_DIR)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS        = 1024

BASE_SYSTEM = """Du bist SwingIQ Coach, ein präziser KI-Golf-Trainer. Antworte auf Deutsch, knapp und praxisnah.
- Beziehe dich immer auf die mitgegebenen Messwerte
- Direkt, ehrlich, motivierend — max. 5 Sätze außer bei explizitem Trainingsplan
- Körpergefühl-Beschreibungen und mentale Bilder sind erwünscht"""

# ── Schemas ───────────────────────────────────────────────────────────────────
class ChatMsg(BaseModel):
    role: str; content: str

class ChatRequest(BaseModel):
    messages:   list[ChatMsg]
    swing_data: Optional[dict] = None
    profi_ref:  Optional[dict] = None
    session_id: Optional[int]  = None

class SaveSessionRequest(BaseModel):
    label:str="Schwunganalyse"; club_type:str="Driver"
    score:int=0; notes:str=""; metrics:list[dict]=[]

class SessionOut(BaseModel):
    id:int; label:str; club_type:str
    score:Optional[int]; created_at:datetime; notes:Optional[str]
    class Config: from_attributes=True

# ── Helpers ───────────────────────────────────────────────────────────────────
def build_system(sd, pr):
    p = BASE_SYSTEM
    if sd:
        p += "\n\nMessdaten:\n" + "\n".join(f"- {k}: {v.get('v','?')}{v.get('u','')} (Ideal:{v.get('ideal','?')}, {v.get('status','?')})" for k,v in sd.items())
    if pr:
        p += f"\n\nProfi ({pr.get('name','?')}): " + ", ".join(f"{k}:{v}" for k,v in pr.items() if k!="name")
    return p

async def claude_stream(messages, system) -> AsyncGenerator[bytes, None]:
    if not ANTHROPIC_API_KEY:
        yield b'data: {"error":"ANTHROPIC_API_KEY not set"}\n\n'; return
    hdrs = {"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"}
    payload = {"model":CLAUDE_MODEL,"max_tokens":MAX_TOKENS,"system":system,"stream":True,"messages":messages}
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST",ANTHROPIC_URL,json=payload,headers=hdrs) as resp:
            if resp.status_code!=200:
                yield f"data: {(await resp.aread()).decode()}\n\n".encode(); return
            async for line in resp.aiter_lines():
                if line: yield f"{line}\n\n".encode()

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.post("/auth/register", response_model=TokenResponse)
async def register(req:RegisterRequest, db:Session=Depends(get_db)):
    return await register_handler(req, db)

@app.post("/auth/login", response_model=TokenResponse)
async def login(req:LoginRequest, db:Session=Depends(get_db)):
    return await login_handler(req, db)

@app.get("/auth/me", response_model=UserOut)
async def me(user:User=Depends(get_current_user)):
    return user

# ── Chat ──────────────────────────────────────────────────────────────────────
@app.post("/chat/stream")
async def chat_stream(req:ChatRequest, user:Optional[User]=Depends(get_optional_user), db:Session=Depends(get_db)):
    system   = build_system(req.swing_data, req.profi_ref)
    messages = [m.model_dump() for m in req.messages]
    if user and req.messages and req.messages[-1].role=="user":
        save_chat_message(db, user.id, "user", req.messages[-1].content, req.session_id)

    async def streamer():
        full=""
        async for chunk in claude_stream(messages, system):
            try:
                line=chunk.decode().strip()
                if line.startswith("data: "):
                    ev=json.loads(line[6:])
                    if ev.get("type")=="content_block_delta":
                        full+=ev.get("delta",{}).get("text","")
            except: pass
            yield chunk
        if user and full:
            save_chat_message(db, user.id, "assistant", full, req.session_id)

    return StreamingResponse(streamer(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.get("/chat/history")
async def chat_history(session_id:Optional[int]=None, limit:int=40, user:User=Depends(get_current_user), db:Session=Depends(get_db)):
    msgs=get_chat_history(db,user.id,session_id,limit)
    return [{"role":m.role,"content":m.content,"created_at":str(m.created_at)} for m in msgs]

# ── Sessions ──────────────────────────────────────────────────────────────────
@app.post("/sessions", response_model=SessionOut)
async def save_session(req:SaveSessionRequest, user:User=Depends(get_current_user), db:Session=Depends(get_db)):
    return create_session(db,user.id,req.label,req.club_type,req.score,req.notes,req.metrics)

@app.get("/sessions", response_model=list[SessionOut])
async def list_sessions(user:User=Depends(get_current_user), db:Session=Depends(get_db)):
    return get_user_sessions(db,user.id)

@app.get("/sessions/{sid}")
async def get_session(sid:int, user:User=Depends(get_current_user), db:Session=Depends(get_db)):
    s=db.query(SwingSession).filter(SwingSession.id==sid,SwingSession.user_id==user.id).first()
    if not s: raise HTTPException(404,"Session nicht gefunden.")
    return {"id":s.id,"label":s.label,"club_type":s.club_type,"score":s.score,"notes":s.notes,
            "created_at":s.created_at,"metrics":[{"name":m.name,"value":m.value,"unit":m.unit,"status":m.status} for m in s.metrics]}

@app.delete("/sessions/{sid}")
async def delete_session(sid:int, user:User=Depends(get_current_user), db:Session=Depends(get_db)):
    s=db.query(SwingSession).filter(SwingSession.id==sid,SwingSession.user_id==user.id).first()
    if not s: raise HTTPException(404,"Session nicht gefunden.")
    db.delete(s); db.commit()
    return {"deleted":sid}

# ── VIDEO UPLOAD & ANALYSIS ───────────────────────────────────────────────────
ALLOWED_EXTS = {".mp4",".mov",".avi",".webm",".mkv"}
MAX_SIZE_MB   = 500

def _run_analysis_job(job_id: str, video_path: Path):
    """Blocking analysis — runs in threadpool via asyncio.to_thread."""
    from video_processor import analyze_video
    def progress(pct, msg):
        _jobs[job_id].update({"progress": pct, "message": msg})

    try:
        _jobs[job_id]["status"] = "processing"
        result = analyze_video(str(video_path), progress_callback=progress)
        _jobs[job_id].update({
            "status":   "done",
            "progress": 100,
            "message":  "Analyse abgeschlossen",
            "result": {
                "swing_data":       result.to_api_dict(),
                "overall_score":    result.overall_score(),
                "fps":              result.fps,
                "total_frames":     result.total_frames,
                "analyzed_frames":  result.analyzed_frames,
                "backend":          result.backend,
                "annotated_frames": result.annotated_frames,
                "phases": {
                    "address":   result.phases.address,
                    "takeaway":  result.phases.takeaway,
                    "top":       result.phases.top,
                    "downswing": result.phases.downswing,
                    "impact":    result.phases.impact,
                    "follow":    result.phases.follow,
                },
            }
        })
    except Exception as e:
        _jobs[job_id].update({"status":"error","progress":0,"error":str(e)})
    finally:
        # Clean up uploaded file
        try: video_path.unlink(missing_ok=True)
        except: pass


@app.post("/upload")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: Optional[User] = Depends(get_optional_user),
):
    """
    Upload a swing video. Returns a job_id immediately.
    Poll /upload/status/{job_id} or stream /upload/progress/{job_id} for updates.
    """
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Ungültiges Format. Erlaubt: {', '.join(ALLOWED_EXTS)}")

    # Read + size check
    data = await file.read()
    size_mb = len(data) / (1024*1024)
    if size_mb > MAX_SIZE_MB:
        raise HTTPException(413, f"Datei zu groß ({size_mb:.1f}MB). Max: {MAX_SIZE_MB}MB")

    # Save to disk
    job_id    = str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{job_id}{ext}"
    save_path.write_bytes(data)

    # Init job state
    _jobs[job_id] = {
        "status":   "queued",
        "progress": 0,
        "message":  "In Warteschlange…",
        "result":   None,
        "error":    None,
        "user_id":  user.id if user else None,
        "filename": file.filename,
        "size_mb":  round(size_mb, 2),
    }

    # Run analysis in background thread (non-blocking)
    background_tasks.add_task(asyncio.to_thread, _run_analysis_job, job_id, save_path)

    return {"job_id": job_id, "status": "queued", "size_mb": round(size_mb,2)}


@app.get("/upload/status/{job_id}")
async def upload_status(job_id: str):
    """Poll for analysis job status."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' nicht gefunden.")
    return {k: v for k,v in job.items() if k != "result"} | {"has_result": job.get("result") is not None}


@app.get("/upload/result/{job_id}")
async def upload_result(job_id: str):
    """Get full result once job is done."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' nicht gefunden.")
    if job["status"] == "error":
        raise HTTPException(422, job.get("error","Analyse fehlgeschlagen."))
    if job["status"] != "done":
        raise HTTPException(202, "Analyse läuft noch.")
    return job["result"]


@app.get("/upload/progress/{job_id}")
async def upload_progress_stream(job_id: str):
    """
    Server-Sent Events stream of analysis progress.
    Frontend can listen and get real-time percentage + message.
    """
    if job_id not in _jobs:
        raise HTTPException(404, f"Job '{job_id}' nicht gefunden.")

    async def event_gen():
        while True:
            job = _jobs.get(job_id, {})
            payload = json.dumps({
                "job_id":   job_id,
                "status":   job.get("status","unknown"),
                "progress": job.get("progress",0),
                "message":  job.get("message",""),
                "error":    job.get("error"),
            })
            yield f"data: {payload}\n\n"

            status = job.get("status","")
            if status in ("done","error"):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


# ── Util ──────────────────────────────────────────────────────────────────────
@app.get("/")
def root(): return {"service":"SwingIQ API","version":"3.1.0","status":"ok"}

@app.get("/health")
def health(): return {"healthy":True,"api_key_set":bool(ANTHROPIC_API_KEY)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app",host="0.0.0.0",port=int(os.getenv("PORT",8000)),reload=True)
