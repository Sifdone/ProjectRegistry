# src/project_tracker/task_tracker.py
import os, json, threading, uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from waitress import serve

DATA = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "ProjectTracker")
CFG  = os.path.join(DATA, "config.json")
CUR  = os.path.join(DATA, "current_session.json")
LOG  = os.path.join(DATA, "sessions.jsonl")
REG  = os.path.join(DATA, "projects.json")

# cached registry (reload on mtime change)
_REG_LOCK = threading.Lock()
_REG_CACHE = {"mtime": None, "ids": set()}

def now(): return datetime.now(timezone.utc) 
def iso(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

def ensure():
    os.makedirs(DATA, exist_ok=True)
    if not os.path.exists(CFG):
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump({"api_key": os.urandom(24).hex(), "port": 5123, "idle_minutes": 20}, f, indent=2)

def _registry_ids():
    """Return a set of known project IDs, or None if registry file is missing."""
    try:
        mtime = os.stat(REG).st_mtime_ns
    except FileNotFoundError:
        return None
    with _REG_LOCK:
        if _REG_CACHE["mtime"] != mtime:
            try:
                with open(REG, "r", encoding="utf-8") as f:
                    items = json.load(f)
                ids = {p.get("id") for p in items if isinstance(p, dict) and p.get("id")}
            except Exception:
                ids = set()
            _REG_CACHE["ids"] = ids
            _REG_CACHE["mtime"] = mtime
        return _REG_CACHE["ids"]

def _project_exists(pid: str):
    ids = _registry_ids()
    if ids is None:
        return None   # registry missing
    return pid in ids


def load_cfg():
    with open(CFG, "r", encoding="utf-8") as f: return json.load(f)

def _read_json(path):
    if not os.path.exists(path): return None
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f)

def append_session(entry):
    with open(LOG, "a", encoding="utf-8") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def stop_current(reason="manual"):
    cur = _read_json(CUR)
    if not cur: return False
    start = datetime.fromisoformat(cur["start"].replace("Z","+00:00"))
    end = now()
    append_session({
        "session_id": cur["session_id"],
        "project_id": cur["project_id"],
        "task": cur.get("task"),
        "start": cur["start"],
        "end": iso(end),
        "duration_sec": int((end - start).total_seconds()),
        "stop_reason": reason
    })
    try: os.remove(CUR)
    except FileNotFoundError: pass
    return True

ensure()
cfg = load_cfg()
app = Flask(__name__)

def _auth_ok(req): return req.headers.get("X-API-Key") == cfg["api_key"]

@app.get("/health")
def health(): return {"ok": True, "ts": iso(now())}

@app.get("/state")
def state():
    if not _auth_ok(request): return ("unauthorized", 401)
    return jsonify(_read_json(CUR))

@app.post("/start")
def start():
    if not _auth_ok(request): return ("unauthorized", 401)
    body = request.get_json(force=True, silent=True) or {}
    pid = body.get("projectId"); task = body.get("task")
    if not pid: return ({"error":"projectId required"}, 400)
    
    # NEW: validate against registry
    exists = _project_exists(pid)
    if exists is None:
        return ({"error": "registry_missing",
                 "hint": "No projects.json found. Create a project with `pm init` first."}, 409)
    if not exists:
        return ({"error": "unknown_project_id", "projectId": pid}, 404)

    # 
    stop_current("switch")
    cur = {
        "session_id": f"S-{now():%Y%m%d}-{uuid.uuid4().hex[:6]}",
        "project_id": pid,
        "task": task,
        "start": iso(now()),
        "last_ping": iso(now())
    }
    _write_json(CUR, cur); _reset_idle_timer()
    return {"ok": True}

@app.post("/stop")
def stop():
    if not _auth_ok(request): return ("unauthorized", 401)
    reason = (request.get_json(silent=True) or {}).get("reason","manual")
    return {"ok": True, "stopped": stop_current(reason)}

@app.post("/ping")
def ping():
    if not _auth_ok(request): return ("unauthorized", 401)
    cur = _read_json(CUR)
    if not cur: return {"ok": True, "active": False}
    cur["last_ping"] = iso(now()); _write_json(CUR, cur); _reset_idle_timer()
    return {"ok": True, "active": True}

@app.get("/projects")
def projects():
    if not _auth_ok(request): return ("unauthorized", 401)
    ids = _registry_ids()
    if ids is None:
        return ({"error": "registry_missing"}, 409)
    return {"projectIds": sorted(ids)}


# --- idle auto-stop
_idle_lock = threading.Lock()
_idle_timer = None
def _idle_fire(): stop_current("inactivity")
def _reset_idle_timer():
    global _idle_timer
    with _idle_lock:
        if _idle_timer: _idle_timer.cancel()
        minutes = int(load_cfg().get("idle_minutes", 20))
        _idle_timer = threading.Timer(minutes*60, _idle_fire); _idle_timer.daemon = True; _idle_timer.start()

def main():
    ensure(); cfg = load_cfg()
    serve(app, host="127.0.0.1", port=int(cfg.get("port", 5123)))

if __name__ == "__main__":
    main()