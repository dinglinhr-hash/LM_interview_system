"""
app/services/email_service.py
─────────────────────────────────────────────────────────────
Sends HTML interview invitation emails via SMTP (async).
"""
from jinja2 import Environment, FileSystemLoader
import logging
from typing import Optional, List, Dict
from datetime import date, time, datetime, timedelta, timezone
import uuid

from app.config import settings



logger = logging.getLogger(__name__)

template_env = Environment(
    loader=FileSystemLoader("templates")
)

def _format_time(t) -> str:
    if isinstance(t, time):
        return t.strftime("%H:%M")
    if isinstance(t, str):
        return t[:5]
    return str(t)


def _build_invitation_html(
    applicant_name: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    meet_link: Optional[str],
    is_update: bool = False,
    company_name: Optional[str] = None,
) -> str:
    action_template = "更新" if is_update else ""
    company_display = company_name if company_name else "本公司"
    action = f"{action_template} {company_display}面試邀約" if action_template else f"{company_display}面試邀約"
    template = template_env.get_template("invite_email.html")
    return template.render(
        applicant_name=applicant_name,
        position_title=position_title,
        slot_date=slot_date,
        start_time=_format_time(start_time),
        end_time=_format_time(end_time),
        meet_link=meet_link,
        is_update=is_update,
        action=action,
    )


async def send_otp_email(to_email: str, otp_code: str) -> bool:
    """發送帳號變更驗證碼到指定 email。"""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured – skipping OTP email to %s", to_email)
        return False

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        subject = "【HR 帳號變更】Email 驗證碼"
        html_body = f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px;
                    border: 1px solid #e2e8f0; border-radius: 12px; background: #fff;">
          <h2 style="font-size: 18px; color: #333; margin-bottom: 8px;">HR 帳號變更驗證</h2>
          <p style="color: #555; font-size: 14px; margin-bottom: 24px;">
            您正在申請修改 HR 帳號資料，請使用以下驗證碼完成確認：
          </p>
          <div style="text-align: center; margin: 24px 0;">
            <span style="font-size: 36px; font-weight: bold; letter-spacing: 10px;
                         color: #e8961a; background: #fffbeb; padding: 12px 24px;
                         border-radius: 8px; display: inline-block;">
              {otp_code}
            </span>
          </div>
          <p style="color: #888; font-size: 12px; text-align: center;">
            驗證碼 10 分鐘內有效，請勿將驗證碼提供給他人。
          </p>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.EMAIL_FROM
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )

        logger.info("OTP email sent to %s", to_email)
        return True

    except Exception as exc:
        logger.error("Failed to send OTP email to %s: %s", to_email, exc)
        return False


async def send_interview_invitation(
    to_email: str,
    applicant_name: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    meet_link: Optional[str] = None,
    is_update: bool = False,
    company_name: Optional[str] = None,
) -> bool:

    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured – skipping email to %s", to_email)
        return False

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        company_display = company_name if company_name else "本公司"
        subject = (
            f"[更新] {company_display}面試邀約 – {position_title}"
            if is_update
            else f"{company_display}面試邀約 – {position_title}"
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.EMAIL_FROM
        msg["To"]      = to_email

        html_body = _build_invitation_html(
            applicant_name, position_title, slot_date,
            start_time, end_time, meet_link, is_update, company_name,
        )
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )

        logger.info("Email sent to %s (update=%s)", to_email, is_update)
        return True

    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False


def _build_ics_content(
    event_id: str,
    applicant_name: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    meet_link: Optional[str],
    organizer_email: str,
    attendees: List[Dict[str, str]],
) -> str:
    """
    生成 iCalendar (.ics) 格式內容

    Args:
        event_id: Google Calendar event ID (用作 UID)
        applicant_name: 應徵者名字
        position_title: 職位名稱
        slot_date: 面試日期
        start_time: 開始時間
        end_time: 結束時間
        meet_link: Google Meet 連結
        organizer_email: 組織者（發起者）Email
        attendees: 參與者列表 [{"name": "...", "email": "..."}]
    """
    tz_offset = timezone(timedelta(hours=8))
    start_dt = datetime.combine(slot_date, start_time).replace(tzinfo=tz_offset)
    end_dt = datetime.combine(slot_date, end_time).replace(tzinfo=tz_offset)

    # 格式化為 UTC ISO 8601
    start_utc = start_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end_utc = end_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    summary = f"面試：{applicant_name} – {position_title}"
    description = f"應徵者：{applicant_name}\n職位：{position_title}"
    if meet_link:
        description += f"\n會議連結：{meet_link}"

    # 構建 ATTENDEE 行
    attendee_lines = []
    for attendee in attendees:
        email = attendee.get("email", "").replace("@", "%40")
        name = attendee.get("name", "")
        attendee_lines.append(
            f'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={name}:mailto:{email}'
        )
    attendee_str = "\r\n".join(attendee_lines)

    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-Interview Platform//EN
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:{event_id}@interview-platform
DTSTAMP:{now_utc}
DTSTART:{start_utc}
DTEND:{end_utc}
SUMMARY:{summary}
DESCRIPTION:{description}
LOCATION:{meet_link if meet_link else ""}
ORGANIZER;CN=Interview Platform:mailto:{organizer_email}
{attendee_str}
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT
END:VCALENDAR"""

    return ics


async def send_booking_status_notification(
    event_type: str,
    applicant_name: str,
    applicant_email: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    new_status: Optional[str] = None,
    old_status: Optional[str] = None,
) -> bool:
    """
    發送預約狀態通知給 HR（SMTP_USER）。

    觸發時機：
      - event_type="new_booking"   : 應徵者完成新預約（BookingStatus.completed）
      - event_type="status_change" : HR 將狀態變更為
            auto_completed / no_show / canceled

    Args:
        event_type:       "new_booking" 或 "status_change"
        applicant_name:   應徵者姓名
        applicant_email:  應徵者 Email
        position_title:   職位名稱
        slot_date:        面試日期
        start_time:       面試開始時間
        end_time:         面試結束時間
        new_status:       變更後狀態（status_change 時必填）
        old_status:       變更前狀態（status_change 時選填，用於郵件說明）
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured – skipping booking status notification")
        return False

    to_email = settings.SMTP_USER  # 通知發送給 HR 帳號本身

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        # ── 標題與主旨依事件類型區分 ────────────────────────────
        _STATUS_LABELS: dict[str, str] = {
            "completed":      "完成預約",
            "auto_completed": "已出席面試",
            "no_show":        "未出席",
            "canceled":       "已取消預約",
        }

        if event_type == "new_booking":
            subject = f"【新預約】{applicant_name} – {position_title}"
            status_row = ""
        else:
            new_label = _STATUS_LABELS.get(new_status or "", new_status or "")
            old_label = _STATUS_LABELS.get(old_status or "", old_status or "") if old_status else ""
            change_text = f"{old_label} → {new_label}" if old_label else new_label
            subject = f"【{new_label}】{applicant_name} – {position_title}"
            status_row = f"""
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">狀態變更</td>
              <td style="padding:6px 0;font-size:14px;font-weight:600;color:#e8961a;">
                {change_text}
              </td>
            </tr>"""

        html_body = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;
                    padding:32px 24px;border:1px solid #e2e8f0;
                    border-radius:12px;background:#fff;">

          <table style="width:100%;border-collapse:collapse;">
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;width:90px;">應徵者</td>
              <td style="padding:6px 0;font-size:14px;">{applicant_name}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">Email</td>
              <td style="padding:6px 0;font-size:14px;">
                <a href="mailto:{applicant_email}" style="color:#3b82f6;text-decoration:none;">
                  {applicant_email}
                </a>
              </td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">職位</td>
              <td style="padding:6px 0;font-size:14px;">{position_title}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">面試日期</td>
              <td style="padding:6px 0;font-size:14px;">
                {slot_date.strftime("%Y年%m月%d日") if hasattr(slot_date, "strftime") else slot_date}
              </td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">面試時間</td>
              <td style="padding:6px 0;font-size:14px;">
                {_format_time(start_time)} – {_format_time(end_time)}
              </td>
            </tr>
            {status_row}
          </table>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.EMAIL_FROM
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )

        logger.info(
            "Booking status notification sent to %s (event=%s, applicant=%s)",
            to_email, event_type, applicant_email,
        )
        return True

    except Exception as exc:
        logger.error(
            "Failed to send booking status notification to %s: %s", to_email, exc
        )
        return False


async def send_applicant_modified_notification(
    applicant_name: str,
    applicant_email: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    changed_fields: List[str],
    old_position_title: Optional[str] = None,
) -> bool:
    """
    通知 HR：應徵者在「應徵者預約」頁面自行修改了預約資料。

    觸發時機：
      - 應徵者呼叫 /api/bookings/modify 且實際變更了姓名、電話、
        應徵職位、或面試時段中的任一項。

    Args:
        applicant_name:      修改後的應徵者姓名
        applicant_email:     應徵者 Email
        position_title:      修改後的職位名稱
        slot_date:           修改後的面試日期
        start_time:          修改後的面試開始時間
        end_time:            修改後的面試結束時間
        changed_fields:      實際變更的欄位中文名稱清單，例如 ["姓名", "面試時段"]
        old_position_title:  變更前的職位名稱（若職位被改變時用於顯示對照）
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured – skipping applicant-modified notification")
        return False

    to_email = settings.SMTP_USER  # 通知發送給 HR 帳號本身

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        changed_summary = "、".join(changed_fields) if changed_fields else "資料"
        subject = f"【應徵者修改資料】{applicant_name} – {position_title}"

        position_row = ""
        if "應徵職位" in changed_fields and old_position_title and old_position_title != position_title:
            position_row = f"""
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">應徵職位</td>
              <td style="padding:6px 0;font-size:14px;">
                <span style="color:#999;text-decoration:line-through;">{old_position_title}</span>
                &nbsp;→&nbsp;
                <span style="font-weight:600;">{position_title}</span>
              </td>
            </tr>"""
        else:
            position_row = f"""
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">應徵職位</td>
              <td style="padding:6px 0;font-size:14px;">{position_title}</td>
            </tr>"""

        html_body = f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;
                    padding:32px 24px;border:1px solid #e2e8f0;
                    border-radius:12px;background:#fff;">

          <p style="font-size:14px;color:#e8961a;font-weight:600;margin:0 0 16px;">
            應徵者已自行修改了：{changed_summary}
          </p>

          <table style="width:100%;border-collapse:collapse;">
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;width:90px;">應徵者</td>
              <td style="padding:6px 0;font-size:14px;">{applicant_name}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">Email</td>
              <td style="padding:6px 0;font-size:14px;">
                <a href="mailto:{applicant_email}" style="color:#3b82f6;text-decoration:none;">
                  {applicant_email}
                </a>
              </td>
            </tr>
            {position_row}
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">面試日期</td>
              <td style="padding:6px 0;font-size:14px;">
                {slot_date.strftime("%Y年%m月%d日") if hasattr(slot_date, "strftime") else slot_date}
              </td>
            </tr>
            <tr>
              <td style="padding:6px 0;color:#666;font-size:14px;">面試時間</td>
              <td style="padding:6px 0;font-size:14px;">
                {_format_time(start_time)} – {_format_time(end_time)}
              </td>
            </tr>
          </table>

          <p style="font-size:12px;color:#999;margin:20px 0 0;">
            如需查看完整修改前後對照，請至 HR 後台該預約的「歷史紀錄」查看。
          </p>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.EMAIL_FROM
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
        )

        logger.info(
            "Applicant-modified notification sent to %s (applicant=%s, fields=%s)",
            to_email, applicant_email, changed_fields,
        )
        return True

    except Exception as exc:
        logger.error(
            "Failed to send applicant-modified notification to %s: %s", to_email, exc
        )
        return False


async def send_interviewer_ics_invitation(
    interviewer_emails: List[Dict[str, str]],
    applicant_name: str,
    position_title: str,
    slot_date: date,
    start_time,
    end_time,
    meet_link: Optional[str] = None,
    event_id: Optional[str] = None,
) -> bool:
    """
    發送 ICS 日曆邀請給內部參與者（面試官）

    Args:
        interviewer_emails: [{"name": "Alice 陳", "email": "alice@example.com"}]
        applicant_name: 應徵者名字
        position_title: 職位名稱
        slot_date: 面試日期
        start_time: 開始時間
        end_time: 結束時間
        meet_link: Google Meet 連結
        event_id: Google Calendar event ID
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured – skipping ICS email to interviewers")
        return False

    if not interviewer_emails:
        logger.info("No interviewers to notify")
        return False

    # 使用 event_id 或生成新的 UUID
    ics_event_id = event_id or str(uuid.uuid4())
    organizer_email = settings.EMAIL_FROM or "noreply@interview-platform.local"

    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        ics_content = _build_ics_content(
            event_id=ics_event_id,
            applicant_name=applicant_name,
            position_title=position_title,
            slot_date=slot_date,
            start_time=start_time,
            end_time=end_time,
            meet_link=meet_link,
            organizer_email=organizer_email,
            attendees=interviewer_emails,
        )

        # 為每個內部參與者發送郵件
        for interviewer in interviewer_emails:
            to_email = interviewer.get("email")
            to_name = interviewer.get("name", to_email)

            if not to_email:
                logger.warning("Interviewer has no email, skipping")
                continue

            subject = f"面試邀約：{applicant_name} – {position_title}"

            # 構建 MIME multipart message（含 text/calendar 部分）
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = to_email
            msg["Method"] = "REQUEST"

            # 添加純文本版本
            text_body = f"""
            親愛的 {to_name}，
            有新的面試邀約需要您出席：

            應徵者：{applicant_name}
            職位：{position_title}
            日期：{slot_date.strftime("%Y年%m月%d日")}
            時間：{_format_time(start_time)} – {_format_time(end_time)}

            {f"會議連結：{meet_link}" if meet_link else "會議連結將另行通知"}

            感謝您的配合！
            """
            msg.attach(MIMEText(text_body, "plain", "utf-8"))

            # 添加 iCalendar 部分
            ics_part = MIMEText(ics_content, "calendar", "utf-8")
            ics_part.add_header("Content-Disposition", "attachment", filename="invite.ics")
            ics_part.add_header("Content-ID", f"<event@{organizer_email}>")
            ics_part.add_header("Content-Method", "REQUEST")
            msg.attach(ics_part)

            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                start_tls=True,
            )

            logger.info("ICS invitation sent to interviewer %s", to_email)

        return True

    except Exception as exc:
        logger.error("Failed to send ICS invitations: %s", exc)
        return False