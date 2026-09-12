"""Private Telegram delivery/ack transport. It never changes claim verdicts."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.error
import urllib.request

from . import maintenance

DEFAULTS = {"timeout_seconds": 30, "max_attempts": 3, "retry_seconds": 60,
            "unread_reminder_seconds": 86400, "poll_seconds": 20}
METHODS = {"getMe", "getWebhookInfo", "sendMessage", "getUpdates", "answerCallbackQuery"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS notice (
 id TEXT PRIMARY KEY, repo TEXT NOT NULL, subject TEXT NOT NULL, revision TEXT NOT NULL,
 message TEXT NOT NULL, chat_id TEXT NOT NULL, users TEXT NOT NULL,
 nonce TEXT NOT NULL UNIQUE, status TEXT NOT NULL, message_id INTEGER,
 attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
 created REAL NOT NULL, sent REAL, acknowledged REAL, ack_source TEXT, error TEXT,
 UNIQUE(repo,subject,revision));
CREATE TABLE IF NOT EXISTS transport (key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


class DeliveryError(RuntimeError):
    def __init__(self, message, *, uncertain=False, retry_after=None):
        super().__init__(message)
        self.uncertain, self.retry_after = uncertain, retry_after


def _token(path):
    p = Path(path).expanduser().resolve()
    if p.stat().st_mode & 0o077:
        raise ValueError("token file must not be readable by group/others; use mode 0600")
    token = p.read_text().strip()
    if not token or ":" not in token or not token.split(":", 1)[0].isdigit() or any(c.isspace() for c in token):
        raise ValueError("invalid Telegram token file")
    return token


def api(token_file, method, payload=None, *, timeout=30, bot_id=None):
    if method not in METHODS:
        raise ValueError("unsupported Telegram operation")
    token = _token(token_file)
    if bot_id is not None and token.split(":", 1)[0] != str(bot_id):
        raise ValueError("token now identifies another bot; configure that bot explicitly")
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/" + method,
        data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        # Never include a URL or provider text that might echo the token.
        retry_after = None
        try:
            retry_after = json.loads(exc.read(65536)).get("parameters", {}).get("retry_after")
        except (ValueError, AttributeError):
            pass
        raise DeliveryError(f"Telegram rejected request (HTTP {exc.code})", retry_after=retry_after) from None
    except (OSError, ValueError) as exc:
        raise DeliveryError("Telegram transport did not yield a valid response", uncertain=method == "sendMessage") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise DeliveryError("Telegram did not accept the operation")
    return data.get("result")


def _config_path(root):
    return maintenance.private_dir(root) / "telegram.json"


def configuration(root):
    p = _config_path(root)
    return json.loads(p.read_text()) if p.is_file() else None


def configure(root, *, token_file, chat_id, user_ids=None, storage_root=None, limits=None):
    chat_id = str(int(chat_id))
    users = [int(x) for x in (user_ids or ([int(chat_id)] if int(chat_id) > 0 else []))]
    if not users:
        raise ValueError("group notifications require explicit allowed user_ids for acknowledgement")
    if limits is not None and (not isinstance(limits, dict) or set(limits) - set(DEFAULTS)):
        raise ValueError("unknown transport limits; use " + ", ".join(sorted(DEFAULTS)))
    limits = {**DEFAULTS, **(limits or {})}
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in limits.values()):
        raise ValueError("transport limits must be positive numbers")
    token_file = str(Path(token_file).expanduser().resolve())
    identity = api(token_file, "getMe", timeout=limits["timeout_seconds"])
    if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
        raise DeliveryError("Telegram bot identity could not be read")
    bot = str(identity["id"])
    directory = (Path(storage_root).expanduser() if storage_root else Path.home() / ".local/share/vibeproof/telegram") / bot
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"bot": bot, "chat_id": chat_id, "users": users, "token_file": token_file,
            "storage": str(directory.resolve()), "limits": limits}
    p = _config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with maintenance.exclusive(root):
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2); f.write("\n")
    with closing(_db(data)) as c, c:
        c.execute("UPDATE notice SET status='superseded' WHERE repo=? AND (chat_id!=? OR users!=?)",
                  (_repo(root), chat_id, json.dumps(users)))
    return {"configured": True, "bot": bot, "chat_id": chat_id, "receiver": "not yet observed"}


def _db(cfg, *, readonly=False):
    path = Path(cfg["storage"]) / "transport.sqlite"
    if readonly:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        c = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        c.executescript(SCHEMA)
    c.row_factory = sqlite3.Row
    return c


def _repo(root):
    return str(maintenance.private_dir(root).resolve())


def _public(row):
    return {k: row[k] for k in ("id", "subject", "revision", "status", "message_id", "attempts", "sent", "acknowledged", "ack_source", "error")}


def enqueue(root, *, subject, revision, message):
    cfg = configuration(root)
    if not cfg:
        raise ValueError("Telegram has not been configured")
    if not subject or not revision or not message or len(message) > 4000:
        raise ValueError("subject, revision and a message of at most 4000 characters are required")
    repo = _repo(root)
    identity = hashlib.sha256(json.dumps([repo, subject, revision]).encode()).hexdigest()
    with closing(_db(cfg)) as c, c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT * FROM notice WHERE id=?", (identity,)).fetchone()
        if existing:
            if existing["message"] != message:
                raise ValueError("same notice revision cannot describe a different message")
            return _public(existing)
        c.execute("UPDATE notice SET status='superseded' WHERE repo=? AND subject=? AND status!='resolved'", (repo, subject))
        c.execute("INSERT INTO notice(id,repo,subject,revision,message,chat_id,users,nonce,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (identity, repo, subject, revision, message, cfg["chat_id"], json.dumps(cfg["users"]), secrets.token_urlsafe(18), "pending", time.time()))
        return _public(c.execute("SELECT * FROM notice WHERE id=?", (identity,)).fetchone())


def deliver(root, identity, *, retry_unknown=False):
    cfg = configuration(root)
    if not cfg:
        raise ValueError("Telegram has not been configured")
    with closing(_db(cfg)) as c:
        with c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM notice WHERE id=? AND repo=?", (identity, _repo(root))).fetchone()
            if row is None:
                raise ValueError("unknown notification")
            if row["chat_id"] != cfg["chat_id"] or json.loads(row["users"]) != cfg["users"]:
                raise ValueError("notification recipient authorization changed")
            if row["status"] in ("sending", "unknown") and not retry_unknown:
                return {**_public(row), "action_required": "delivery may already exist; reconcile or explicitly retry this uncertainty"}
            if row["status"] in ("acknowledged", "superseded", "resolved"):
                return _public(row)
            limits = cfg["limits"]
            if row["status"] == "sent" and time.time() < row["sent"] + limits["unread_reminder_seconds"]:
                return _public(row)
            if row["attempts"] >= limits["max_attempts"] or row["next_try"] > time.time():
                return _public(row)
            c.execute("UPDATE notice SET status='sending',attempts=attempts+1,error=NULL WHERE id=?", (identity,))
        try:
            message = api(cfg["token_file"], "sendMessage", {
                "chat_id": row["chat_id"], "text": row["message"],
                "reply_markup": {"inline_keyboard": [[{"text": "已讀 / Acknowledge", "callback_data": "ack:" + row["nonce"]}]]}},
                timeout=limits["timeout_seconds"], bot_id=cfg["bot"])
            if not isinstance(message, dict) or not isinstance(message.get("message_id"), int):
                raise DeliveryError("Telegram response omitted the message identity", uncertain=True)
        except DeliveryError as exc:
            with c:
                c.execute("UPDATE notice SET status=?,error=?,next_try=? WHERE id=? AND status='sending'",
                          ("unknown" if exc.uncertain else "failed", str(exc), time.time() + (exc.retry_after or limits["retry_seconds"]), identity))
        except (OSError, ValueError):
            with c:
                c.execute("UPDATE notice SET status='failed',error=?,next_try=? WHERE id=? AND status='sending'",
                          ("local credential/config unavailable; request was not completed", time.time() + limits["retry_seconds"], identity))
        else:
            with c:
                # A simultaneous callback/new revision wins over the network reply.
                c.execute("UPDATE notice SET status='sent',message_id=?,sent=? WHERE id=? AND status='sending'", (message["message_id"], time.time(), identity))
        return _public(c.execute("SELECT * FROM notice WHERE id=?", (identity,)).fetchone())


def acknowledge(root, identity, *, source="local operator request"):
    cfg = configuration(root)
    if not cfg:
        raise ValueError("Telegram has not been configured")
    with closing(_db(cfg)) as c, c:
        row = c.execute("SELECT * FROM notice WHERE id=? AND repo=?", (identity, _repo(root))).fetchone()
        if row is None or row["status"] in ("superseded", "resolved"):
            raise ValueError("notification is absent or no longer the current revision")
        c.execute("UPDATE notice SET status='acknowledged',acknowledged=?,ack_source=? WHERE id=?", (time.time(), source, identity))
        return _public(c.execute("SELECT * FROM notice WHERE id=?", (identity,)).fetchone())


def process_updates(cfg, updates):
    """Acknowledge exact revisions transactionally before advancing the bot cursor."""
    if not isinstance(updates, list):
        raise DeliveryError("Telegram updates must be an array")
    outcomes = []
    with closing(_db(cfg)) as c:
        for update in updates:
            if not isinstance(update, dict):
                raise DeliveryError("Telegram update must be an object")
            uid = update.get("update_id")
            if not isinstance(uid, int) or isinstance(uid, bool):
                raise DeliveryError("Telegram update omitted its identity")
            query = update.get("callback_query") or {}
            if not isinstance(query, dict):
                raise DeliveryError("Telegram callback must be an object")
            callback_id = query.get("id")
            payload = query.get("data", "")
            accepted, reason = False, "Not a current authorized notification"
            with c:
                c.execute("BEGIN IMMEDIATE")
                cursor = c.execute("SELECT value FROM transport WHERE key='offset'").fetchone()
                if cursor and uid < int(cursor[0]):
                    continue
                row = c.execute("SELECT * FROM notice WHERE nonce=?", (payload[4:],)).fetchone() if isinstance(payload, str) and payload.startswith("ack:") else None
                message = query.get("message") or {}
                sender = query.get("from") or {}
                if not isinstance(message, dict) or not isinstance(sender, dict) or not isinstance(message.get("chat", {}), dict):
                    raise DeliveryError("Telegram callback coordinates must be objects")
                chat = str(message.get("chat", {}).get("id", ""))
                user = sender.get("id")
                if row and isinstance(user, int) and not isinstance(user, bool) and chat == row["chat_id"] and user in json.loads(row["users"]) and row["status"] not in ("superseded", "resolved"):
                    c.execute("UPDATE notice SET status='acknowledged',acknowledged=?,ack_source=?,message_id=COALESCE(message_id,?) WHERE id=?",
                              (time.time(), "telegram:" + str(user), message.get("message_id"), row["id"]))
                    accepted, reason = True, "已記錄已讀；不代表問題已修好或批准修改。"
                c.execute("INSERT OR REPLACE INTO transport VALUES('offset',?)", (str(uid + 1),))
                c.execute("INSERT OR REPLACE INTO transport VALUES('last_update_at',?)", (str(time.time()),))
            if callback_id:
                try:
                    api(cfg["token_file"], "answerCallbackQuery", {"callback_query_id": callback_id, "text": reason}, timeout=cfg["limits"]["timeout_seconds"], bot_id=cfg["bot"])
                except (DeliveryError, OSError, ValueError):
                    # Ack is durable; UI acknowledgement failure does not undo it.
                    with c:
                        c.execute("INSERT OR REPLACE INTO transport VALUES('callback_reply_error',?)", (str(time.time()),))
            outcomes.append({"update_id": uid, "accepted": accepted})
    return outcomes


def listen(root, *, once=False):
    import fcntl
    cfg = configuration(root)
    if not cfg:
        raise ValueError("Telegram has not been configured")
    with (Path(cfg["storage"]) / "receiver.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("this bot already has a local receiver") from None
        webhook = api(cfg["token_file"], "getWebhookInfo", timeout=cfg["limits"]["timeout_seconds"], bot_id=cfg["bot"])
        if not isinstance(webhook, dict):
            raise DeliveryError("Telegram webhook observation must be an object")
        if webhook.get("url"):
            raise ValueError("this bot already uses a webhook; it was not removed or taken over")
        failures = 0
        while True:
            with closing(_db(cfg)) as c:
                offset = c.execute("SELECT value FROM transport WHERE key='offset'").fetchone()
            try:
                updates = api(cfg["token_file"], "getUpdates", {
                    "offset": int(offset[0]) if offset else 0, "timeout": int(cfg["limits"]["poll_seconds"]),
                    "allowed_updates": ["callback_query"]},
                    timeout=cfg["limits"]["poll_seconds"] + cfg["limits"]["timeout_seconds"], bot_id=cfg["bot"])
                outcomes = process_updates(cfg, updates)
                with closing(_db(cfg)) as c, c:
                    c.execute("INSERT OR REPLACE INTO transport VALUES('last_poll_at',?)", (str(time.time()),))
                failures = 0
                if once:
                    return {"updates": outcomes}
            except DeliveryError as exc:
                failures += 1
                with closing(_db(cfg)) as c, c:
                    c.execute("INSERT OR REPLACE INTO transport VALUES('receiver_error',?)", (str(exc),))
                if once or failures >= cfg["limits"]["max_attempts"]:
                    raise
                time.sleep(cfg["limits"]["retry_seconds"])


def status(root):
    cfg = configuration(root)
    if not cfg:
        return {"configured": False, "notices": []}
    with closing(_db(cfg, readonly=True)) as c:
        meta = dict(c.execute("SELECT key,value FROM transport"))
        poll = float(meta.get("last_poll_at", "0"))
        return {"configured": True, "bot": cfg["bot"],
                "receiver": meta, "confirmation_gap": not poll or time.time() - poll > 86400,
                "notices": [_public(row) for row in c.execute("SELECT * FROM notice WHERE repo=? ORDER BY created", (_repo(root),))]}


def resolve_claim(root, claim_id):
    from . import attention, ledger
    cfg = configuration(root)
    if not cfg:
        raise ValueError("Telegram has not been configured")
    with closing(ledger.connect_readonly(root)) as conn:
        claim = conn.execute("SELECT task_id FROM claim WHERE id=?", (claim_id,)).fetchone()
        if claim is None:
            raise ValueError("resolve needs an existing claim; use ack for other notices")
        current = attention.scan(root, conn=conn, task_id=claim["task_id"])
        if current["errors"] or any(x["claim"] == claim_id for x in current["items"]):
            raise ValueError("claim is unresolved or its current worktree cannot be verified")
    with closing(_db(cfg)) as c, c:
        c.execute("UPDATE notice SET status='resolved' WHERE repo=? AND subject=?", (_repo(root), claim_id))
    return {"resolved_subject": claim_id, "basis": "current kernel claim state"}


def command(root, action, data, *, identity=None, once=False):
    if action == "ack":
        return acknowledge(root, identity)
    if action == "listen":
        return listen(root, once=once)
    if action == "notifications":
        return status(root)
    if action == "receiver":
        from .notification_receiver import manage
        return manage(root, data.get("operation", "status"))
    operation = data.get("operation", "enqueue")
    if operation == "configure":
        return configure(root, **{k: v for k, v in data.items() if k != "operation"})
    if operation == "deliver":
        return deliver(root, identity or data.get("id"), retry_unknown=data.get("retry_unknown", False))
    if operation == "enqueue":
        return enqueue(root, subject=data.get("subject"), revision=data.get("revision"), message=data.get("message"))
    if operation == "resolve":
        return resolve_claim(root, data.get("subject"))
    if operation == "flush":
        return {"results": [deliver(root, row["id"]) for row in status(root)["notices"] if row["status"] in ("pending", "failed", "sent")]}
    raise ValueError("unknown notification operation")
