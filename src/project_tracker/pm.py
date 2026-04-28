# src/project_tracker/pm.py
import json, os, argparse, uuid, re, sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ProjectTracker"
REG  = DATA / "projects.json"
SESS = DATA / "sessions.jsonl"

def iso_now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def load_projects():
    if REG.exists(): return json.loads(REG.read_text(encoding="utf-8"))
    return []

def save_projects(items):
    DATA.mkdir(parents=True, exist_ok=True)
    REG.write_text(json.dumps(items, indent=2), encoding="utf-8")

def slug_title(title: str) -> str:
    """UPPERCASE, spaces->-, strip invalids (A-Z 0-9 _ -)."""
    s = title.strip().replace(" ", "-")
    s = re.sub(r"[^A-Za-z0-9\-_]+", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.upper()


def next_suffix_from_dir(base_dir: Path) -> int:
    """
    Look at ALL subfolders in base_dir. If a folder's last 3 chars are digits,
    consider them as a candidate. Return max + 1 (or 1 if none).
    """
    max_seen = 0
    if not base_dir.exists():
        return 1
    for p in base_dir.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if len(name) >= 3 and name[-3:].isdigit():
            n = int(name[-3:])
            if n > max_seen:
                max_seen = n
    return max_seen + 1 if max_seen >= 0 else 1

def compute_id_and_path(title: str, base_dir: Path, use_increment: bool) -> tuple[str, Path]:
    """Build project ID and destination folder path per rules."""
    tslug = slug_title(title)
    date_part = datetime.now().strftime("%d%m%y")
    core = f"{tslug}-{date_part}"

    if use_increment:
        nxt = next_suffix_from_dir(base_dir)
        folder_name = f"{core}_{nxt:03d}"
    else:
        folder_name = core

    pid = f"PROJ-{folder_name}"
    dest = base_dir / folder_name
    return pid, dest

# ---------- commands ----------

def cmd_init(name: str, base_dir: Path, use_increment: bool):
    pid, root = compute_id_and_path(name, base_dir, use_increment)

    if root.exists() and not use_increment:
        print(f"Target folder already exists: {root}\n"
              f"Tip: re-run with --inc to auto-append an incremented suffix.", file=sys.stderr)
        sys.exit(1)

    root.mkdir(parents=True, exist_ok=True)
    for d in ["01_Intake","02_Workfiles","03_Exports"]:
        (root / d).mkdir(exist_ok=True)

    meta = {"id": pid, "name": name, "created_at": iso_now(), "notes": ""}
    (root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    items = load_projects()
    items.append({"id": pid, "name": name, "path": str(root), "created_at": iso_now()})
    save_projects(items)

    print(pid)  # print the new project ID

def cmd_list():
    for p in load_projects(): print(p["id"], "-", p.get("name", ""), "-", p.get("path", ""))

def cmd_rollup():
    totals = {}
    if not SESS.exists(): return
    with SESS.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
                totals[j["project_id"]] = totals.get(j["project_id"], 0) + int(j.get("duration_sec",0))
            except: pass
    for k,v in sorted(totals.items(), key=lambda kv: kv[0]):
        hours = v/3600
        print(f"{k}: {hours:.2f}h")

def cmd_remove(project_id: str) -> int:
    items = load_projects()
    new_items = [p for p in items if p.get("id") != project_id]
    if len(new_items) == len(items):
        print(f"Project not found: {project_id}", file=sys.stderr)
        return 1
    save_projects(new_items)
    print(f"Removed {project_id} from registry (files left intact).")
    return 0

def main():
    ap = argparse.ArgumentParser(prog="pm", description="Project Manager CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="Create a new project")
    a.add_argument("name", help="Project title (used in ID)")
    a.add_argument("--dir", help="Base directory to create the project folder under (defaults to CWD)")
    a.add_argument("--inc", action="store_true",
                   help="Enable _NNN suffix determined by scanning existing folders' last 3 digits")

    sub.add_parser("list", help="List registered projects")
    sub.add_parser("rollup", help="Total time per project from sessions.jsonl")

    r = sub.add_parser("remove", help="Remove a project from registry (keeps files)")
    r.add_argument("project_id")

    args = ap.parse_args()

    if args.cmd == "init":
        base = Path(args.dir) if args.dir else Path.cwd()
        cmd_init(args.name, base, args.inc)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "rollup":
        cmd_rollup()
    elif args.cmd == "remove":
        sys.exit(cmd_remove(args.project_id))

if __name__ == "__main__":
    main()


    