"""
Storage — JSON files, which are correct at 50-100 users.

Ported from the prototype's storage layer. Phase 1 replaces it with SQLite
and Phase 2 with PostgreSQL; there is no abstraction here anticipating
either, because we do not need one yet.

WHAT IS AND IS NOT KEPT
-----------------------
`data/` is gitignored and holds birth data, which is personal information
under 個人情報保護法. That is a deliberate, necessary retention: a chart
cannot be recomputed without it.

Crisis messages are different. `log_crisis_event` records the timestamp and
which pattern fired and **never the message text or the user id**. We need
the event for monitoring and the weekly review; we do not need the words.
A JSON file of users' suicidal messages is a serious harm if breached, and
mental-health information is likely 要配慮個人情報, which carries stricter
obligations than the rest of this file handles.

TODO(legal): retention and deletion policy for everything here goes to
counsel in Phase 1. Until then the default is to keep less.

Three defects in the prototype's version are fixed rather than carried:

  * `_consume_free_quota` read, checked and wrote outside the lock, so two
    concurrent messages could both pass the check. It is one locked
    operation here.
  * Quota reset used server-local time. For a Japan-only product the free
    tier resets on JST or a user's "today" moves with the host's timezone.
  * `datetime.utcnow()` is deprecated and returns a naive datetime.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

JST = timezone(timedelta(hours=9))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jst_today() -> str:
    """Today in Japan, as YYYY-MM-DD.

    The free tier resets on the user's day, not the server's.
    """
    return datetime.now(JST).strftime("%Y-%m-%d")


class Storage:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_file = self.data_dir / "users.json"
        self.llm_log_file = self.data_dir / "llm_usage.jsonl"
        self.events_file = self.data_dir / "events.jsonl"
        self.review_queue_file = self.data_dir / "manual_review.jsonl"
        self._lock = threading.RLock()
        if not self.users_file.exists():
            self.users_file.write_text("{}", encoding="utf-8")

    # --- Users -------------------------------------------------------------

    def _read_users(self) -> dict:
        try:
            return json.loads(self.users_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_users(self, users: dict) -> None:
        tmp = self.users_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.users_file)

    def get_user(self, user_id: str) -> dict:
        with self._lock:
            return self._read_users().get(user_id, {})

    def upsert_user(self, user_id: str, fields: dict) -> dict:
        with self._lock:
            users = self._read_users()
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "created_at": _now_iso(),
                    "free_quota_used": 0,
                    "message_count": 0,
                }
            users[user_id].update(fields)
            users[user_id]["updated_at"] = _now_iso()
            self._write_users(users)
            return users[user_id]

    def increment_message_count(self, user_id: str) -> None:
        with self._lock:
            users = self._read_users()
            user = users.setdefault(user_id, {
                "user_id": user_id,
                "created_at": _now_iso(),
                "free_quota_used": 0,
                "message_count": 0,
            })
            user["message_count"] = user.get("message_count", 0) + 1
            user["updated_at"] = _now_iso()
            self._write_users(users)

    def consume_free_quota(self, user_id: str, limit: int) -> bool:
        """Spend one free reading. True if there was one to spend.

        Read, check and write happen under a single lock. The prototype held
        the lock for each step separately, which let two concurrent messages
        both observe the same remaining count and both proceed.
        """
        today = jst_today()
        with self._lock:
            users = self._read_users()
            user = users.setdefault(user_id, {
                "user_id": user_id,
                "created_at": _now_iso(),
                "free_quota_used": 0,
                "message_count": 0,
            })

            if user.get("quota_reset_date") != today:
                user["quota_reset_date"] = today
                user["free_quota_used"] = 0

            used = user.get("free_quota_used", 0)
            if used >= limit:
                self._write_users(users)
                return False

            user["free_quota_used"] = used + 1
            user["updated_at"] = _now_iso()
            self._write_users(users)
            return True

    def free_quota_remaining(self, user_id: str, limit: int) -> int:
        with self._lock:
            user = self._read_users().get(user_id, {})
            if user.get("quota_reset_date") != jst_today():
                return limit
            return max(0, limit - user.get("free_quota_used", 0))

    # --- Append-only logs --------------------------------------------------

    def _append(self, path: Path, record: dict) -> None:
        with self._lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> Iterator[dict]:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def log_llm_usage(self, user_id: str, usage: dict) -> None:
        """Record a billed call. Read back by the cost guard, so it is the
        one log that must never be lossy."""
        self._append(self.llm_log_file,
                     {"ts": _now_iso(), "user_id": user_id, **usage})

    def iter_llm_usage(self) -> Iterator[dict]:
        return self._read_jsonl(self.llm_log_file)

    def log_event(self, event_type: str, data: Optional[dict] = None) -> None:
        self._append(self.events_file,
                     {"ts": _now_iso(), "type": event_type, **(data or {})})

    def iter_events(self) -> Iterator[dict]:
        return self._read_jsonl(self.events_file)

    def log_crisis_event(self, pattern: str) -> None:
        """Record that a crisis redirect fired. Timestamp and pattern only.

        Deliberately narrow: there is no parameter through which the message
        text or the user id could be passed, because neither should be here.
        We need the rate for monitoring and the weekly review. We do not need
        to know who said it or what they said, and storing either would turn
        an operational metric into a category of data that needs handling
        we have not built and counsel has not reviewed.
        """
        self._append(self.events_file, {
            "ts": _now_iso(),
            "type": "crisis_redirect",
            "pattern": pattern,
        })

    # --- Manual review queue ----------------------------------------------

    def enqueue_manual_review(self, user_id: str, reason: str,
                              details: dict) -> str:
        """Queue a chart a human must look at. Returns the review id.

        Phase 0's "notification" is this queue plus a WARNING log line and
        /admin/review-queue. That relies on an operator actually looking,
        which is honest for a founder-run pilot and not good enough beyond
        it. A real alert is Phase 1.
        """
        review_id = uuid.uuid4().hex[:12]
        self._append(self.review_queue_file, {
            "review_id": review_id,
            "ts": _now_iso(),
            "status": "open",
            "user_id": user_id,
            "reason": reason,
            **details,
        })
        return review_id

    def open_reviews(self) -> List[dict]:
        return [r for r in self._read_jsonl(self.review_queue_file)
                if r.get("status") == "open"]

    # --- Stats -------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Counts only. LLM spend is the cost guard's to report, not this
        module's, so nothing here has to know how a model is priced."""
        with self._lock:
            users = self._read_users()

        today_jst = jst_today()
        charts = hour_unknown = crisis = 0
        for event in self.iter_events():
            if event.get("type") == "chart_computed":
                charts += 1
                if event.get("hour_known") is False:
                    hour_unknown += 1
            elif event.get("type") == "crisis_redirect":
                crisis += 1

        return {
            "total_users": len(users),
            "users_with_birth_data": sum(
                1 for u in users.values() if u.get("birth_date")),
            "users_active_today_jst": sum(
                1 for u in users.values()
                if (u.get("updated_at") or "").startswith(today_jst)),
            "charts_computed": charts,
            # P6, and the evidence P2 should be ruled on. If this is high,
            # asking for a birth *place* to apply an 18-minute correction is
            # not a serious proposition.
            "charts_without_birth_time": hour_unknown,
            "missing_birth_time_rate": (
                round(hour_unknown / charts, 3) if charts else None),
            "crisis_redirects": crisis,
            "open_manual_reviews": len(self.open_reviews()),
            "data_dir": str(self.data_dir),
        }
