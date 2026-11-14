"""Local platform configuration. Secrets never appear in public snapshots."""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.value = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".platform-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def has_admin(self):
        with self.lock:
            return bool(self.value.get("admin"))

    def create_admin(self, password):
        with self.lock:
            if self.has_admin():
                return False
            salt = secrets.token_hex(16)
            self.value["admin"] = {"salt": salt, "hash": password_hash(password, salt)}
            self._save()
            return True

    def authenticate(self, password):
        with self.lock:
            admin = dict(self.value.get("admin", {}))
        return bool(admin) and hmac.compare_digest(admin["hash"], password_hash(password, admin["salt"]))

    def save_token(self, token, expires_at):
        with self.lock:
            self.value["nasa"] = {"token": token, "enabled": True, "verified": True, "expires_at": expires_at, "updated_at": utc_now(), "verified_at": utc_now(), "error": None}
            self._save()

    def token(self):
        with self.lock:
            return self.value.get("nasa", {}).get("token", "")

    def update_connection(self, **values):
        with self.lock:
            self.value.setdefault("nasa", {}).update(values)
            self._save()

    def mark_invalid(self, token):
        with self.lock:
            # An old in-flight extraction must not invalidate a replacement token.
            if self.token() == token:
                self.update_connection(verified=False, error="NASA rejected the saved token. Replace it with a fresh AppEEARS token.")

    def clear_token(self):
        with self.lock:
            self.value.pop("nasa", None)
            self._save()

    def status(self, admin=False):
        with self.lock:
            data = dict(self.value.get("nasa", {}))
        configured = bool(data.get("token"))
        expired = bool(data.get("expires_at") and datetime.fromisoformat(data["expires_at"]) <= datetime.now(timezone.utc))
        state = "unconfigured"
        if configured:
            state = "paused" if not data.get("enabled") else "expired" if expired else "attention" if not data.get("verified") else "ready"
        messages = {
            "unconfigured": "The administrator is setting up the data connection. You can still choose your region, dates, and variable.",
            "paused": "Data requests are temporarily paused by the administrator. Your previous results remain available.",
            "expired": "The data connection needs an administrator update. Please try again after it has been renewed.",
            "attention": "The data connection needs an administrator update. Please try again after it has been renewed.",
            "ready": "NASA observations are available. Choose your selection to create a heat map.",
        }
        result = {"state": state, "available": state == "ready", "message": messages[state]}
        if admin:
            result.update(configured=configured, enabled=data.get("enabled", False), updated_at=data.get("updated_at"), verified_at=data.get("verified_at"), expires_at=data.get("expires_at"), error=data.get("error"))
        return result
