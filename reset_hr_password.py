# reset_hr_password.py
import asyncio
from app.database import get_db
from app.models.models import HRAdmin
from app.services.auth import hash_password
from sqlalchemy import select

TARGET_EMAIL = "admin@example.com"   # ← 改成你的 HR email
NEW_PASSWORD = "1234"   # ← 改成新密碼

async def reset():
    async for db in get_db():
        result = await db.execute(select(HRAdmin).where(HRAdmin.email == TARGET_EMAIL))
        admin = result.scalar_one_or_none()
        if not admin:
            print(f"找不到 {TARGET_EMAIL}")
            return
        admin.password_hash = hash_password(NEW_PASSWORD)
        await db.commit()
        print(f"✅ {TARGET_EMAIL} 密碼已更新")

asyncio.run(reset())