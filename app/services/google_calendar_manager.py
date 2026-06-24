import base64
import json
import os
import sqlite3
import time
import uuid
import textwrap
import logging
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, List, Tuple

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
TOKEN_FILE = settings.GOOGLE_TOKEN_FILE or "token.json"
CREDENTIALS_FILE = settings.GOOGLE_SERVICE_ACCOUNT_FILE or "google_service_account.json"
SENDER_EMAIL = os.getenv("SENDER_EMAIL", settings.GOOGLE_CALENDAR_ID or "interview@example.com")
DATABASE_FILE = "events.db"
CALENDAR_ID = settings.GOOGLE_CALENDAR_ID or "primary"


# ─────────────────────────────────────────────
# SQLite Event ID Management
# ─────────────────────────────────────────────
def init_db() -> None:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                summary TEXT UNIQUE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                deleted_at DATETIME,
                status TEXT DEFAULT 'active'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hr_oauth_tokens (
                email TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at DATETIME NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")


def save_event_id(summary: str, event_id: str) -> None:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO events (id, summary, status, updated_at)
            VALUES (?, ?, 'active', CURRENT_TIMESTAMP)
        """, (event_id, summary))
        conn.commit()
        conn.close()
        logger.info(f"Event ID saved: {event_id}")
    except Exception as e:
        logger.error(f"Failed to save event ID: {e}")


def load_event_id(summary: str) -> Optional[str]:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM events WHERE summary = ? AND status = 'active'",
            (summary,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Failed to load event ID: {e}")
        return None


def list_saved_events() -> List[Tuple[str, str, str]]:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT summary, id, created_at FROM events WHERE status = 'active' ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Failed to list events: {e}")
        return []


def delete_event_record(summary: str) -> None:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE events SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP WHERE summary = ?",
            (summary,)
        )
        conn.commit()
        conn.close()
        logger.info(f"Event record deleted: {summary}")
    except Exception as e:
        logger.error(f"Failed to delete event record: {e}")


def save_hr_oauth_token(email: str, access_token: str, refresh_token: Optional[str], expires_at: datetime) -> None:
    """儲存 / 更新 HR 的 Google OAuth token。refresh_token 為 None 時保留舊值。"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        if refresh_token:
            cursor.execute("""
                INSERT INTO hr_oauth_tokens (email, access_token, refresh_token, expires_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(email) DO UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    expires_at=excluded.expires_at,
                    updated_at=CURRENT_TIMESTAMP
            """, (email.lower(), access_token, refresh_token, expires_at.isoformat()))
        else:
            cursor.execute("""
                INSERT INTO hr_oauth_tokens (email, access_token, refresh_token, expires_at, updated_at)
                VALUES (?, ?, NULL, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(email) DO UPDATE SET
                    access_token=excluded.access_token,
                    expires_at=excluded.expires_at,
                    updated_at=CURRENT_TIMESTAMP
            """, (email.lower(), access_token, expires_at.isoformat()))
        conn.commit()
        conn.close()
        logger.info(f"HR OAuth token saved for {email}")
    except Exception as e:
        logger.error(f"Failed to save HR OAuth token for {email}: {e}")


def load_hr_oauth_token(email: str) -> Optional[dict]:
    """讀取指定 HR email 的 Google OAuth token。"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT access_token, refresh_token, expires_at FROM hr_oauth_tokens WHERE email = ?",
            (email.lower(),)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {"access_token": row[0], "refresh_token": row[1], "expires_at": row[2]}
    except Exception as e:
        logger.error(f"Failed to load HR OAuth token for {email}: {e}")
        return None


# ─────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────
def _get_user_credentials(email: str):
    """
    嘗試用儲存在 hr_oauth_tokens 的 HR OAuth token 建立 Credentials。
    若 access_token 已過期則用 refresh_token 換新，並寫回資料庫。
    回傳 None 表示該 HR 尚未用 Google 登入過 / 沒有授權 Calendar 權限，需 fallback 用 Service Account。
    """
    token = load_hr_oauth_token(email)
    if not token:
        return None

    from google.oauth2.credentials import Credentials
    from app.services import google_oauth as _google_oauth

    expires_at = datetime.fromisoformat(token["expires_at"])
    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()

    if now >= expires_at - timedelta(minutes=2):
        if not token["refresh_token"]:
            logger.warning(f"HR OAuth access_token expired for {email} and no refresh_token stored.")
            return None

        # 注意：這裡故意呼叫同步版本 refresh_access_token_sync，而不是 async 版本。
        # 本函式常常是從 FastAPI 的 async request handler 內被同步呼叫的，
        # 此時目前 thread 已經有事件迴圈在跑，若改用 asyncio.run()/手動開新迴圈
        # 會撞上 "Cannot run the event loop while another loop is running"，
        # 導致 refresh 靜默失敗、後續 Calendar/Meet 連結建立失敗。
        new_token = _google_oauth.refresh_access_token_sync(token["refresh_token"])

        if not new_token or "access_token" not in new_token:
            logger.error(f"Failed to refresh HR OAuth token for {email}")
            return None

        new_expiry = datetime.now() + timedelta(seconds=new_token.get("expires_in", 3600))
        save_hr_oauth_token(email, new_token["access_token"], None, new_expiry)
        access_token = new_token["access_token"]
    else:
        access_token = token["access_token"]

    return Credentials(token=access_token)


def authenticate(impersonate_email: Optional[str] = None) -> Tuple[object, object]:
    """
    認證策略：
    1. 若 impersonate_email（預設取 CALENDAR_ID/SENDER_EMAIL，即 HR 信箱）已透過 Google 登入並授權 Calendar，
       優先使用該使用者的 OAuth token（這樣才能建立 Google Meet 連結）。
    2. 否則 fallback 回 Service Account（注意：Service Account 無法建立 Meet 連結）。
    """
    target_email = impersonate_email or CALENDAR_ID
    if target_email and "@" in target_email:
        user_creds = _get_user_credentials(target_email)
        if user_creds:
            service = build("calendar", "v3", credentials=user_creds)
            return service, user_creds

    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Service account file '{CREDENTIALS_FILE}' not found."
        )
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
    )
    service = build("calendar", "v3", credentials=creds)
    return service, creds


# ─────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────
def _format_datetime(dt_str: str, tz: str = "Asia/Taipei") -> dict:
    dt_str = dt_str.strip()
    if len(dt_str) == 10:
        return {"date": dt_str}
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": tz}
    except ValueError:
        raise ValueError(
            f"Invalid date format: '{dt_str}'. Use 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'"
        )


def _parse_dt(dt_str: str) -> datetime:
    return datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")


def _to_ical_dt(dt_str: str) -> str:
    dt = _parse_dt(dt_str) - timedelta(hours=8)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _print_event(event: dict, index: int = None) -> str:
    prefix = f"[{index}] " if index is not None else ""
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = start.get("dateTime", start.get("date", "Unknown"))[:16].replace("T", " ")
    end_str = end.get("dateTime", end.get("date", "Unknown"))[:16].replace("T", " ")
    summary = event.get("summary", "(No title)")
    location = event.get("location", "")
    description = event.get("description", "")
    event_id = event.get("id", "")
    meet_link = _extract_meet_link(event)
    attendees = event.get("attendees", [])
    output = f"\n{prefix}📅 {summary}\n"
    output += f"   🕐 {start_str} ~ {end_str}\n"
    if location:
        output += f"   📍 {location}\n"
    if description:
        desc_preview = description[:60] + ("..." if len(description) > 60 else "")
        output += f"   📝 {desc_preview}\n"
    if meet_link:
        output += f"   🎥 Meet: {meet_link}\n"
    if attendees:
        emails = ", ".join(a.get("email", "") for a in attendees)
        output += f"   👥 Attendees: {emails}\n"
    output += f"   🔑 ID: {event_id}\n"
    return output


# ─────────────────────────────────────────────
# iCalendar Functions
# ─────────────────────────────────────────────
def _build_ics(
    uid: str,
    summary: str,
    start: str,
    end: str,
    organizer_email: str,
    attendee_emails: List[str],
    location: str = "",
    description: str = "",
    meet_link: str = "",
    method: str = "REQUEST",
    sequence: int = 0,
) -> str:
    dtstart = _to_ical_dt(start)
    dtend = _to_ical_dt(end)
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    attendee_lines = "\n".join(
        f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;RSVP=TRUE"
        f";CN={email}:mailto:{email}"
        for email in attendee_emails
    )
    conf_line = f"X-GOOGLE-CONFERENCE:{meet_link}\n" if meet_link else ""
    full_description = description
    if meet_link:
        full_description = f"Google Meet: {meet_link}\n\n{description}".strip()

    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Google Inc//GoogleCalendarV1.0//EN\r\n"
        f"METHOD:{method}\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtend}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"ORGANIZER;CN={organizer_email}:mailto:{organizer_email}\r\n"
        f"{attendee_lines}\r\n"
        f"SEQUENCE:{sequence}\r\n"
    )
    if location:
        ics += f"LOCATION:{location}\r\n"
    if full_description:
        desc_folded = textwrap.fill(
            f"DESCRIPTION:{full_description}",
            width=75,
            subsequent_indent=" ",
        )
        ics += desc_folded + "\r\n"
    if conf_line:
        ics += conf_line
    ics += (
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    return ics


def _send_ical_invite(
    to_emails: List[str],
    subject: str,
    summary: str,
    start: str,
    end: str,
    creds,
    location: str = "",
    description: str = "",
    meet_link: str = "",
    uid: str = None,
    method: str = "REQUEST",
    sequence: int = 0,
) -> bool:
    if not to_emails:
        return False
    uid = uid or str(uuid.uuid4())
    ics_content = _build_ics(
        uid=uid, summary=summary, start=start, end=end,
        organizer_email=SENDER_EMAIL, attendee_emails=to_emails,
        location=location, description=description, meet_link=meet_link,
        method=method, sequence=sequence,
    )
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_emails)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("", "plain", "utf-8"))
    cal_mime = MIMEText(ics_content, "calendar", "utf-8")
    cal_mime.set_param("method", method)
    alt.attach(cal_mime)
    msg.attach(alt)
    ics_attachment = MIMEBase("application", "ics")
    ics_attachment.set_payload(ics_content.encode("utf-8"))
    encoders.encode_base64(ics_attachment)
    ics_attachment.add_header("Content-Disposition", "attachment", filename="invite.ics")
    msg.attach(ics_attachment)
    try:
        gmail_service = build("gmail", "v1", credentials=creds)
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail_service.users().messages().send(
            userId="me", body={"raw": raw_message}
        ).execute()
        logger.info(f"iCal invitation sent to {', '.join(to_emails)}")
        return True
    except Exception as e:
        logger.error(f"Failed to send iCal invitation: {e}")
        return False


# ─────────────────────────────────────────────
# Core Functions
# ─────────────────────────────────────────────
def list_events(
    calendar_id: str = None,
    max_results: int = 10,
    days_ahead: int = 30,
    query: str = None,
) -> list:
    try:
        service, _ = authenticate()
        cal = calendar_id or CALENDAR_ID
        now = datetime.now(timezone.utc).isoformat()
        time_max = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
        kwargs = {
            "calendarId": cal,
            "timeMin": now,
            "timeMax": time_max,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if query:
            kwargs["q"] = query
        result = service.events().list(**kwargs).execute()
        return result.get("items", [])
    except Exception as e:
        logger.error(f"Failed to list events: {e}")
        return []


def _extract_meet_link(event: dict) -> str:
    """從 event 物件中取出 Meet 連結；若 entryPoints 不存在則回傳空字串。"""
    entry_points = event.get("conferenceData", {}).get("entryPoints", [])
    for ep in entry_points:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    # 沒有明確標示 video 類型時，退回第一個有 uri 的項目
    for ep in entry_points:
        if ep.get("uri"):
            return ep["uri"]
    return ""


def _wait_for_meet_link(
    service,
    calendar_id: str,
    event_id: str,
    initial_event: dict,
    max_attempts: int = 5,
    delay_seconds: float = 1.5,
) -> str:
    """
    Google Calendar API 的 conferenceData createRequest 是非同步產生的：
    events().insert()/update() 的回應常常還是 status=pending、entryPoints 是空的，
    實際的 Meet 連結要過一小段時間後重新 GET 該 event 才會出現。
    這裡先檢查 insert/update 當下的回應，沒有的話就輪詢重試幾次。
    """
    meet_link = _extract_meet_link(initial_event)
    if meet_link:
        return meet_link

    create_request = initial_event.get("conferenceData", {}).get("createRequest", {})
    status_code = create_request.get("status", {}).get("statusCode")
    logger.info(
        f"No Meet entryPoints in immediate response for event {event_id} "
        f"(createRequest status={status_code}); polling for conferenceData."
    )

    for attempt in range(1, max_attempts + 1):
        time.sleep(delay_seconds)
        try:
            refreshed = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as e:
            logger.warning(f"Polling attempt {attempt} failed to fetch event {event_id}: {e}")
            continue

        meet_link = _extract_meet_link(refreshed)
        if meet_link:
            logger.info(f"Meet link resolved for event {event_id} after {attempt} poll(s).")
            return meet_link

        status_code = (
            refreshed.get("conferenceData", {}).get("createRequest", {}).get("status", {}).get("statusCode")
        )
        if status_code == "failure":
            logger.error(f"Google reported conference createRequest failure for event {event_id}.")
            break

    logger.warning(
        f"Meet link still unavailable for event {event_id} after {max_attempts} poll attempts."
    )
    return ""


def create_event(
    summary: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    calendar_id: str = None,
    tz: str = "Asia/Taipei",
    add_meet: bool = False,
    attendees: Optional[List[str]] = None,
    send_ical: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Create event. attendees should be omitted for Service Account (no DWD)."""
    try:
        service, creds = authenticate()
        cal = calendar_id or CALENDAR_ID
        event_uid = str(uuid.uuid4())

        event = {
            "summary": summary,
            "start": _format_datetime(start, tz),
            "end": _format_datetime(end, tz),
        }
        if location:
            event["location"] = location
        if description:
            event["description"] = description

        conference_version = 0
        if add_meet:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": event_uid,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            conference_version = 1

        created = service.events().insert(
            calendarId=cal,
            body=event,
            conferenceDataVersion=conference_version,
            sendUpdates="none",
        ).execute()

        logger.info(f"Event created: {created.get('id')} on calendar {cal}")

        meet_link = ""
        if add_meet:
            meet_link = _wait_for_meet_link(
                service=service,
                calendar_id=cal,
                event_id=created.get("id"),
                initial_event=created,
            )
        else:
            meet_link = _extract_meet_link(created)

        if add_meet and not meet_link:
            logger.warning(
                f"add_meet=True but no Meet link returned for event {created.get('id')}. "
                f"conferenceData={created.get('conferenceData')}"
            )

        save_event_id(summary, created.get("id"))
        return meet_link, created.get("id")
    except Exception as e:
        logger.error(f"Failed to create event: {e}")
        return None, None


def update_event(
    event_id: str,
    summary: str = None,
    start: str = None,
    end: str = None,
    location: str = None,
    description: str = None,
    add_meet: bool = None,
    attendees: Optional[List[str]] = None,
    send_ical: bool = False,
    calendar_id: str = None,
    tz: str = "Asia/Taipei",
) -> Tuple[Optional[str], Optional[str]]:
    """原地更新 Google Calendar 行程"""
    try:
        service, creds = authenticate()
        cal = calendar_id or CALENDAR_ID

        existing = service.events().get(calendarId=cal, eventId=event_id).execute()
        ical_uid = existing.get("iCalUID", event_id)
        new_sequence = existing.get("sequence", 0) + 1

        event = existing.copy()
        event["sequence"] = new_sequence

        if summary is not None:
            event["summary"] = summary
        if start is not None:
            event["start"] = _format_datetime(start, tz)
        if end is not None:
            event["end"] = _format_datetime(end, tz)
        if location is not None:
            event["location"] = location
        if description is not None:
            event["description"] = description

        # add_meet=True 時補上 createRequest 才會真的產生 Meet 連結；add_meet=False 則移除既有會議資料
        conference_version = 0
        if add_meet is False:
            event.pop("conferenceData", None)
        elif add_meet is True:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            conference_version = 1

        updated = service.events().update(
            calendarId=cal,
            eventId=event_id,
            body=event,
            conferenceDataVersion=conference_version,
            sendUpdates="none",
        ).execute()

        logger.info(f"Event updated in-place: {event_id}, sequence: {new_sequence}")

        if add_meet is True:
            meet_link = _wait_for_meet_link(
                service=service,
                calendar_id=cal,
                event_id=updated.get("id", event_id),
                initial_event=updated,
            )
        else:
            meet_link = _extract_meet_link(updated)

        return meet_link, updated.get("id")
    except Exception as e:
        logger.error(f"Failed to update event: {e}", exc_info=True)
        return None, None


def delete_event(
    event_id: str,
    calendar_id: str = None,
    send_ical: bool = False,
) -> bool:
    try:
        service, creds = authenticate()
        cal = calendar_id or CALENDAR_ID
        event = service.events().get(calendarId=cal, eventId=event_id).execute()
        logger.info(f"Deleting event: {event_id}")

        service.events().delete(
            calendarId=cal,
            eventId=event_id,
            sendUpdates="none",
        ).execute()

        delete_event_record(event.get('summary', ''))
        logger.info(f"Event deleted: {event_id}")
        return True
    except HttpError as e:
        logger.error(f"Failed to delete event: {e}")
        return False


def get_event(
    event_id: str,
    calendar_id: str = None,
) -> Optional[dict]:
    try:
        service, _ = authenticate()
        cal = calendar_id or CALENDAR_ID
        event = service.events().get(calendarId=cal, eventId=event_id).execute()
        logger.info(f"Retrieved event: {event_id}")
        return event
    except HttpError as e:
        logger.error(f"Failed to get event: {e}")
        return None


def recreate_meet_link(
    event_id: str,
    applicant_name: str,
    applicant_email: str,
    position_title: str,
    slot_date: object,
    start_time: object,
    end_time: object,
    send_ical: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """Delete old event and create new one with fresh Meet link."""
    if event_id:
        try:
            delete_event(event_id, send_ical=False)
        except Exception as e:
            logger.warning(f"Failed to delete old event {event_id}: {e}")

    start_str = f"{slot_date.strftime('%Y-%m-%d')} {start_time.strftime('%H:%M')}"
    end_str = f"{slot_date.strftime('%Y-%m-%d')} {end_time.strftime('%H:%M')}"

    return create_event(
        summary=f"{applicant_name} – {position_title}",
        start=start_str,
        end=end_str,
        description=f"Applicant: {applicant_name}\nEmail: {applicant_email}\nPosition: {position_title}",
        add_meet=True,
        send_ical=False,
    )