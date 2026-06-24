# backfill_meet_links.py
"""
一次性補建腳本：
找出所有 google_calendar_event_id 存在、但 google_meet_link 為空（NULL 或空字串）
的 Booking 紀錄，逐一呼叫 google_calendar_manager.update_event(..., add_meet=True)
向 Google Calendar 補建 Meet 連結，並把結果寫回資料庫。

使用方式：
    1. 先乾跑一次確認會處理哪些紀錄（不會真的呼叫 Google API、不會寫入資料庫）：
         python backfill_meet_links.py --dry-run

    2. 確認沒問題後，正式執行：
         python backfill_meet_links.py

    3. 如果同時想把補好的連結重新發信通知應徵者，加上 --notify：
         python backfill_meet_links.py --notify

注意事項：
    - update_event() 內部呼叫 Google API 是同步（blocking）函式，
      這裡用 asyncio.to_thread 包起來，避免卡住整個 async 事件迴圈。
    - 每筆之間加入小延遲，避免短時間內大量打 Calendar API 被限流（429）。
    - 此腳本只處理「行程已存在（google_calendar_event_id 不為空）但缺連結」的紀錄；
      如果 google_calendar_event_id 本身也是空的，代表行程從未建立成功，
      不在這次補建範圍內，需要另外用 create_event() 處理。
"""
import argparse
import asyncio
import logging

from sqlalchemy import select, or_, func

from app.database import AsyncSessionLocal
from app.models.models import Booking, Position
from app.services import google_calendar_manager
from app.services import email_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_meet_links")

# 兩次 API 呼叫之間的延遲秒數，避免觸發 Google API 速率限制
DELAY_BETWEEN_CALLS = 1.0


async def _debug_dump(db) -> None:
    """除錯用：列出目前連線的資料庫位置，以及 bookings 表的整體狀況。"""
    from app.config import settings

    masked_url = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
    logger.info(f"目前連線的資料庫：...@{masked_url}")

    total_result = await db.execute(select(func.count()).select_from(Booking))
    total_count = total_result.scalar_one()
    logger.info(f"bookings 表總筆數：{total_count}")

    if total_count == 0:
        logger.warning(
            "bookings 表是空的！這代表目前連到的資料庫，"
            "跟平台網頁版實際在用的資料庫可能不是同一個（例如不同的 PostgreSQL 服務、"
            "不同的 .env、或資料庫名稱不同）。請確認 .env 裡的 DATABASE_URL。"
        )
        return

    sample_result = await db.execute(
        select(
            Booking.id,
            Booking.applicant_name,
            Booking.google_calendar_event_id,
            Booking.google_meet_link,
        ).limit(10)
    )
    rows = sample_result.all()
    logger.info("前 10 筆紀錄的相關欄位現況：")
    for row in rows:
        event_id_display = row.google_calendar_event_id or "(空)"
        meet_link_display = row.google_meet_link or "(空)"
        logger.info(
            f"  booking_id={row.id} applicant={row.applicant_name} "
            f"event_id={event_id_display} meet_link={meet_link_display}"
        )


async def _fetch_target_bookings(db):
    """找出 event_id 存在、但 meet_link 是 NULL 或空字串的紀錄"""
    result = await db.execute(
        select(Booking).where(
            Booking.google_calendar_event_id.isnot(None),
            Booking.google_calendar_event_id != "",
            or_(
                Booking.google_meet_link.is_(None),
                Booking.google_meet_link == "",
            ),
        )
    )
    return result.scalars().all()


async def backfill(dry_run: bool = False, notify: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        bookings = await _fetch_target_bookings(db)

        if not bookings:
            logger.info("沒有需要補建 Meet 連結的紀錄。以下列出除錯資訊：")
            await _debug_dump(db)
            return

        logger.info(f"找到 {len(bookings)} 筆缺少 Meet 連結的紀錄。")

        if dry_run:
            for b in bookings:
                logger.info(
                    f"[dry-run] booking_id={b.id} applicant={b.applicant_name} "
                    f"email={b.applicant_email} event_id={b.google_calendar_event_id}"
                )
            logger.info("Dry-run 結束，未呼叫 Google API、未寫入資料庫。")
            return

        success_count = 0
        fail_count = 0

        for b in bookings:
            logger.info(
                f"處理 booking_id={b.id} applicant={b.applicant_name} "
                f"event_id={b.google_calendar_event_id} ..."
            )
            try:
                # update_event 是同步函式，丟到 thread pool 執行避免卡住事件迴圈
                meet_link, returned_event_id = await asyncio.to_thread(
                    google_calendar_manager.update_event,
                    event_id=b.google_calendar_event_id,
                    attendees=[b.applicant_email],
                    add_meet=True,
                )
            except Exception as e:
                logger.error(f"booking_id={b.id} 呼叫 update_event 發生例外：{e}", exc_info=True)
                fail_count += 1
                await asyncio.sleep(DELAY_BETWEEN_CALLS)
                continue

            if not meet_link:
                logger.warning(
                    f"booking_id={b.id} 仍未取得 Meet 連結（可能 Google 端建立失敗，"
                    f"或該 event_id 在 Google Calendar 上已不存在）。"
                )
                fail_count += 1
                await asyncio.sleep(DELAY_BETWEEN_CALLS)
                continue

            b.google_meet_link = meet_link
            if returned_event_id:
                b.google_calendar_event_id = returned_event_id
            await db.commit()
            await db.refresh(b)
            success_count += 1
            logger.info(f"booking_id={b.id} 補建成功，meet_link={meet_link}")

            if notify:
                try:
                    position_title = ""
                    company_name = ""
                    if b.position_id:
                        pos_result = await db.execute(
                            select(Position).where(Position.id == b.position_id)
                        )
                        position = pos_result.scalar_one_or_none()
                        if position:
                            position_title = position.title or ""
                            company_name = position.company or ""

                    await email_service.send_interview_invitation(
                        to_email=b.applicant_email,
                        applicant_name=b.applicant_name,
                        position_title=position_title,
                        slot_date=b.slot_date,
                        start_time=b.start_time,
                        end_time=b.end_time,
                        meet_link=b.google_meet_link,
                        is_update=True,
                        company_name=company_name,
                    )
                    logger.info(f"booking_id={b.id} 已重新寄送通知信給 {b.applicant_email}")
                except Exception as e:
                    logger.error(f"booking_id={b.id} 寄送通知信失敗：{e}", exc_info=True)

            await asyncio.sleep(DELAY_BETWEEN_CALLS)

        logger.info(
            f"補建完成。成功 {success_count} 筆，失敗/仍缺連結 {fail_count} 筆，"
            f"共處理 {len(bookings)} 筆。"
        )


def main():
    parser = argparse.ArgumentParser(description="補建缺少 Google Meet 連結的預約紀錄")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出會被處理的紀錄，不呼叫 Google API、不寫入資料庫",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="補建成功後重新寄送邀約信通知應徵者最新的 Meet 連結",
    )
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run, notify=args.notify))


if __name__ == "__main__":
    main()