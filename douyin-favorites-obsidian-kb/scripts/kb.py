"""Original local knowledge-base utilities. No collector or cloud API bundled."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def resolved(base, value):
    return (base / value).resolve()


def configure(path):
    path = Path(path).resolve()
    cfg = read_json(path)
    cfg["vaults"] = {k: resolved(path.parent, v) for k, v in cfg["vaults"].items()}
    cfg["state_dir"] = resolved(path.parent, cfg["state_dir"])
    if cfg["default_vault"] not in cfg["vaults"]:
        raise ValueError("default_vault is not configured")
    cfg["state_dir"].mkdir(parents=True, exist_ok=True)
    cfg["config_dir"] = path.parent
    return cfg


@contextlib.contextmanager
def lock(state):
    handle = (state / "run.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def database(state):
    db = sqlite3.connect(state / "ledger.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, status TEXT, path TEXT, title TEXT, category TEXT, vault TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    return db


def identity(item, cfg):
    source, source_id = item.get("source"), str(item.get("id", ""))
    if source not in ("douyin", "tiktok", "book", "personal") or not source_id.strip():
        raise ValueError("source or stable id is invalid")
    vault = item.get("vault", cfg["default_vault"])
    if vault not in cfg["vaults"]:
        raise ValueError("unknown vault")
    return json.dumps([vault, source, source_id], ensure_ascii=False), vault


def cutoff_allows(item, cfg):
    cutoff = cfg.get("publication_since")
    if not cutoff or item.get("source") not in ("douyin", "tiktok"):
        return True
    value = item.get("published_at")
    if not isinstance(value, str):
        return False
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    boundary = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    if when.tzinfo is None or boundary.tzinfo is None:
        raise ValueError("publication dates must include timezone")
    return when >= boundary


def slug(text):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]#^]', "_", str(text)).strip(" .")[:80]
    if not clean or clean.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        return "note"
    return clean


def atomic(path, text, backups):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backups / (uuid.uuid4().hex + path.suffix))
    pending = path.with_name(path.name + ".pending-" + uuid.uuid4().hex)
    try:
        pending.write_text(text, encoding="utf-8")
        os.replace(pending, path)
        if path.read_text(encoding="utf-8") != text:
            raise IOError("read-back verification failed")
    finally:
        pending.unlink(missing_ok=True)


def suppress_missing(db):
    for key, path in db.execute("SELECT key,path FROM entries WHERE status='stored'").fetchall():
        if not Path(path).is_file():
            db.execute("UPDATE entries SET status='suppressed' WHERE key=?", (key,))
    db.commit()


def baseline(db, data, cfg):
    if data.get("complete") is not True:
        raise ValueError("baseline requires a complete snapshot; partial scans are rejected")
    prepared = [(identity(item, cfg)[0],) for item in data["items"]]
    with db:
        db.executemany("INSERT OR IGNORE INTO entries(key,status) VALUES (?,'baseline')", prepared)
        db.execute("INSERT OR REPLACE INTO settings VALUES ('baseline_complete','true')")
    return {"baseline_ids": len(set(k for k, in prepared)), "notes_created": 0}


def render(item, key, vault, destination, input_dir, cfg):
    for name in ("title", "summary", "body", "category"):
        if not isinstance(item.get(name), str) or not item[name].strip():
            raise ValueError("missing prepared note field: " + name)
    if len(item["body"].strip()) < 40:
        raise ValueError("body is too short; provide a prepared knowledge note")
    if re.search(r"<(script|iframe)\b|javascript:", item["body"], re.I):
        raise ValueError("active content is not permitted")
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    category = slug(item["category"])
    meta = {k: item.get(k) for k in ("id", "source", "kind", "title", "author", "url", "published_at", "category", "subcategory", "tags")}
    lines = ["---", *[f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()], "---", "", "# " + item["title"], "", "## 一句话摘要", "", item["summary"], "", item["body"], "", f"分类：[[外源资料/分类/{category}|{item['category']}]]", "", "## 来源", "", "- 来源 ID：" + str(item["id"])]
    if item.get("url"):
        if not re.match(r"^https?://[^\s]+$", item["url"]):
            raise ValueError("source URL must be HTTP(S)")
        lines.append("- 原链接：" + item["url"])
    for number, ref in enumerate(item.get("assets", []), 1):
        asset = (input_dir / ref).resolve()
        if not asset.is_relative_to(input_dir) or not asset.is_file():
            raise ValueError("asset must be a local file within the input directory")
        if asset.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            raise ValueError("unsupported image format")
        relative = Path("外源资料/assets") / digest / f"{number:03d}{asset.suffix.lower()}"
        target = vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, target)
        lines.extend(["", f"![[{relative.as_posix()}]]"])
    if item.get("raw_text"):
        lines.extend(["", "## 原始资料", "", "<details><summary>原始文字与摘录（未经优化）</summary><pre>", html.escape(item["raw_text"]), "</pre></details>"])
    return "\n".join(lines) + "\n"


def indexes(db, cfg):
    for vault_name, root in cfg["vaults"].items():
        groups = {}
        for path, title, category in db.execute("SELECT path,title,category FROM entries WHERE status='stored' AND vault=?", (vault_name,)):
            if Path(path).is_file():
                groups.setdefault(category, []).append((Path(path), title))
        navigation = ["# 主题导航", ""]
        for category, notes in sorted(groups.items()):
            safe_category = slug(category)
            navigation.append(f"- [[外源资料/分类/{safe_category}|{category}]]（{len(notes)}）")
            content = ["# " + category, "", "[[外源资料/_主题导航|主题导航]]", ""]
            for path, title in sorted(notes):
                content.append(f"- [[{path.relative_to(root).with_suffix('').as_posix()}|{title.replace('|', ' ').replace(']', '')}]]")
            atomic(root / "外源资料/分类" / (safe_category + ".md"), "\n".join(content) + "\n", cfg["state_dir"] / "backups")
        atomic(root / "外源资料/_主题导航.md", "\n".join(navigation) + "\n", cfg["state_dir"] / "backups")


def import_items(db, data, cfg, mode, input_dir):
    if mode == "new" and not db.execute("SELECT 1 FROM settings WHERE key='baseline_complete'").fetchone():
        raise ValueError("new mode requires an initialized complete baseline")
    suppress_missing(db)
    result = {"stored": 0, "skipped": 0, "queued": 0, "failed": []}
    attempted, limit = 0, int(cfg.get("process_limit", 20))
    if limit < 1:
        raise ValueError("process_limit must be positive")
    seen = set()
    for item in data["items"]:
        key = None
        try:
            key, vault_name = identity(item, cfg)
            previous = db.execute("SELECT status FROM entries WHERE key=?", (key,)).fetchone()
            if key in seen or not cutoff_allows(item, cfg) or (previous and (previous[0] in ("stored", "suppressed") or (previous[0] == "baseline" and mode == "new"))):
                result["skipped"] += 1
                continue
            seen.add(key)
            if attempted >= limit:
                result["queued"] += 1
                with db:
                    db.execute("INSERT OR IGNORE INTO entries(key,status) VALUES (?,'pending')", (key,))
                continue
            attempted += 1
            vault = cfg["vaults"][vault_name]
            digest = hashlib.sha256(key.encode()).hexdigest()[:16]
            destination = vault / "外源资料/笔记" / f"{slug(item.get('title', 'note'))}-{digest}.md"
            text = render(item, key, vault, destination, input_dir, cfg)
            if destination.exists():
                if destination.read_text(encoding="utf-8") != text:
                    raise ValueError("existing note differs; refusing overwrite")
            else:
                atomic(destination, text, cfg["state_dir"] / "backups")
            with db:
                db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)", (key, "stored", str(destination), item["title"], item["category"], vault_name))
            result["stored"] += 1
            indexes(db, cfg)
        except (ValueError, OSError, KeyError) as error:
            result["failed"].append({"id": item.get("id"), "error": str(error)[:400]})
            if key:
                with db:
                    db.execute("INSERT OR IGNORE INTO entries(key,status) VALUES (?,'pending')", (key,))
    return result


def sync(db, cfg):
    if not db.execute("SELECT 1 FROM settings WHERE key='baseline_complete'").fetchone():
        raise ValueError("initialize a complete baseline before daily sync")
    command = cfg.get("adapter_command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("configure an independently installed adapter_command first")
    suppress_missing(db)
    with tempfile.TemporaryDirectory(dir=cfg["state_dir"]) as temp:
        temp = Path(temp)
        request = {"mode": "new", "output_path": str(temp / "response.json"), "publication_since": cfg.get("publication_since"), "process_limit": cfg.get("process_limit", 20), "known": [{"identity": json.loads(key), "status": status} for key, status in db.execute("SELECT key,status FROM entries")]}
        (temp / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(command + ["--request", str(temp / "request.json")], cwd=cfg["config_dir"], timeout=cfg.get("adapter_timeout_seconds", 1800))
        if completed.returncode:
            raise RuntimeError("external adapter failed; exit=" + str(completed.returncode))
        return import_items(db, read_json(temp / "response.json"), cfg, "new", temp)


def search(cfg, query):
    results = []
    for name, root in cfg["vaults"].items():
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8-sig")
            match = text.casefold().find(query.casefold())
            if match >= 0:
                results.append({"vault": name, "path": str(path), "excerpt": text[max(0, match-80):match+240]})
                if len(results) >= 30:
                    return results
    return results


def main():
    parser = argparse.ArgumentParser(description="Local Obsidian knowledge-base utilities")
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("baseline", "import"):
        p = commands.add_parser(name)
        p.add_argument("--input", required=True)
        if name == "import":
            p.add_argument("--mode", choices=("new", "backfill"), default="new")
    commands.add_parser("sync")
    commands.add_parser("status")
    commands.add_parser("ready")
    commands.add_parser("index")
    p = commands.add_parser("search")
    p.add_argument("query")
    args = parser.parse_args()
    cfg = configure(args.config)
    if args.command == "search":
        print(json.dumps(search(cfg, args.query), ensure_ascii=False, indent=2))
        return 0
    with lock(cfg["state_dir"]), contextlib.closing(database(cfg["state_dir"])) as db:
        if args.command == "baseline":
            result = baseline(db, read_json(args.input), cfg)
        elif args.command == "import":
            result = import_items(db, read_json(args.input), cfg, args.mode, Path(args.input).resolve().parent)
        elif args.command == "sync":
            result = sync(db, cfg)
        elif args.command == "ready":
            if not db.execute("SELECT 1 FROM settings WHERE key='baseline_complete'").fetchone() or not cfg.get("adapter_command"):
                raise ValueError("complete baseline and adapter_command are required")
            result = {"ready": True}
        elif args.command == "index":
            suppress_missing(db)
            indexes(db, cfg)
            result = {"indexes_updated": True}
        else:
            result = dict(db.execute("SELECT status,COUNT(*) FROM entries GROUP BY status"))
        atomic(cfg["state_dir"] / "last-report.json", json.dumps(result, ensure_ascii=False, indent=2), cfg["state_dir"] / "backups")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("failed") else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Operation stopped: " + str(error))
        raise SystemExit(1)
