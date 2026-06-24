"""
app/routers/hr_auth.py  –  Login / logout for HR admins
"""
from fastapi import APIRouter, Depends, HTTPException, Response, Form, Query
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
import json
import random
import string
from datetime import datetime, timedelta, timezone
from typing import List as _List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.models import HRAdmin
from app.schemas.schemas import HRLoginResponse, HRUpdateCredentials, HRAdminOut, HRAdminCreate, HRAdminUpdate
from app.services.email_service import send_otp_email
from app.services.auth import verify_password, create_access_token, get_current_hr, hash_password
from app.services import google_oauth
from app.config import settings

router = APIRouter(prefix="/api/hr", tags=["HR Auth"])

# 暫存 OTP：{ email: { "code": str, "expires_at": datetime, "pending": dict } }
_otp_store: dict = {}

def _generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


@router.post("/login", response_model=HRLoginResponse)
async def hr_login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    remember: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == form_data.username, HRAdmin.is_active == True)
    )
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(form_data.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token({"sub": admin.email})

    if remember:
        response.set_cookie(
            key="hr_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
    else:
        response.set_cookie(
            key="hr_token",
            value=token,
            httponly=True,
            samesite="lax",
        )

    return HRLoginResponse(ok=True, access_token=token)


@router.post("/logout")
async def hr_logout(response: Response):
    response.delete_cookie("hr_token")
    return {"ok": True}


@router.get("/me")
async def get_current_hr_info(
    _hr: str = Depends(get_current_hr),
):
    """Return current HR email for authentication check."""
    return {"email": _hr}


@router.post("/me/request-otp")
async def request_otp(
    body: HRUpdateCredentials,
    current_email: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """
    步驟一：驗證舊密碼，通過後發送 OTP 驗證碼到目前的 email。
    同時將 pending 的新帳密暫存起來，等 OTP 驗證通過後才真正寫入。
    """
    result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == current_email, HRAdmin.is_active == True)
    )
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="HR 帳號不存在")

    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status_code=401, detail="目前密碼錯誤")

    if not body.new_email and not body.new_password:
        raise HTTPException(status_code=400, detail="請至少填寫新 Email 或新密碼其中一項")

    if body.new_email and body.new_email != admin.email:
        existing = await db.execute(
            select(HRAdmin).where(HRAdmin.email == body.new_email)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="此 Email 已被使用")

    otp = _generate_otp()
    _otp_store[current_email] = {
        "code": otp,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "pending": {
            "new_email": body.new_email,
            "new_password": body.new_password,
        },
    }

    sent = await send_otp_email(current_email, otp)
    if not sent:
        raise HTTPException(status_code=500, detail="驗證碼寄送失敗，請確認 SMTP 設定")

    return {"ok": True, "message": f"驗證碼已寄送至 {current_email}，請於 10 分鐘內完成驗證"}


@router.post("/me/verify-otp")
async def verify_otp_and_update(
    otp_code: str,
    current_email: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """
    步驟二：輸入收到的 OTP 驗證碼，驗證通過後才真正更新帳密。
    """
    record = _otp_store.get(current_email)
    if not record:
        raise HTTPException(status_code=400, detail="尚未申請驗證碼，請先送出變更申請")

    if datetime.now(timezone.utc) > record["expires_at"]:
        _otp_store.pop(current_email, None)
        raise HTTPException(status_code=400, detail="驗證碼已過期，請重新申請")

    if record["code"] != otp_code.strip():
        raise HTTPException(status_code=400, detail="驗證碼錯誤")

    result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == current_email, HRAdmin.is_active == True)
    )
    admin = result.scalar_one_or_none()
    if not admin:
        _otp_store.pop(current_email, None)
        raise HTTPException(status_code=404, detail="HR 帳號不存在")

    pending = record["pending"]
    if pending.get("new_email"):
        admin.email = pending["new_email"]
    if pending.get("new_password"):
        admin.password_hash = hash_password(pending["new_password"])

    await db.commit()
    _otp_store.pop(current_email, None)

    return {"ok": True, "email": admin.email}


# ─────────────────────────────────────────────────────────────
# Google OAuth2 Login
# ─────────────────────────────────────────────────────────────

@router.get("/google-login")
async def google_login():
    """Redirect to Google OAuth2 authorization page."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth2 is not configured. Please set GOOGLE_CLIENT_ID in .env"
        )

    auth_url = await google_oauth.get_google_authorization_url(
        redirect_uri=settings.GOOGLE_HR_REDIRECT_URI,
        include_calendar_scope=True,
    )
    return RedirectResponse(url=auth_url)


@router.get("/google-callback")
async def google_callback(
    code: str = Query(None),
    error: str = Query(None),
    state: str = Query(None),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth2 callback."""
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Google authorization failed: {error}"
        )

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Authorization code not provided"
        )

    token_data = await google_oauth.exchange_code_for_token(
        code=code,
        redirect_uri=settings.GOOGLE_HR_REDIRECT_URI
    )
    if not token_data:
        raise HTTPException(
            status_code=400,
            detail="Failed to exchange authorization code for token"
        )

    access_token = token_data.get("access_token")
    id_token = token_data.get("id_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="No access token in response"
        )

    user_info = await google_oauth.get_user_info(access_token)
    if not user_info:
        raise HTTPException(
            status_code=400,
            detail="Failed to fetch user information from Google"
        )

    email = user_info.get("email", "").lower()
    name = user_info.get("name", "")

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve email from Google account"
        )

    result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == email, HRAdmin.is_active == True)
    )
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(
            status_code=403,
            detail=f"Google account '{email}' is not authorized as HR admin. Please contact system administrator."
        )

    jwt_token = create_access_token({"sub": admin.email})

    # 儲存 access_token / refresh_token，後續建立行程時用這個 HR 帳號的權限
    # 來呼叫 Google Calendar API，才能正確建立 Meet 連結（Service Account 做不到）。
    from datetime import datetime as _dt, timedelta as _td
    from app.services import google_calendar_manager as _gcal
    _gcal.save_hr_oauth_token(
        email=admin.email,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=_dt.now() + _td(seconds=expires_in),
    )

    if response:
        response.set_cookie(
            key="hr_token",
            value=jwt_token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )

    redirect_url = f"{settings.APP_BASE_URL}/hr?token={jwt_token}&email={email}"
    return RedirectResponse(url=redirect_url)


@router.post("/google-login-token")
async def google_login_with_token(
    id_token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Alternative Google login endpoint for frontend-based token exchange.
    Frontend can send the ID token directly.
    """
    if not id_token:
        raise HTTPException(
            status_code=400,
            detail="id_token is required"
        )

    claims = await google_oauth.verify_google_token(id_token)
    if not claims:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired Google token"
        )

    email = claims.get("email", "").lower()
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve email from Google token"
        )

    result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == email, HRAdmin.is_active == True)
    )
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(
            status_code=403,
            detail=f"Google account '{email}' is not authorized as HR admin"
        )

    jwt_token = create_access_token({"sub": admin.email})

    response.set_cookie(
        key="hr_token",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )

    return HRLoginResponse(ok=True, access_token=jwt_token)


# ─────────────────────────────────────────────────────────────
# HR Admin Management  (瀏覽 / 新增 / 修改 / 停用所有 HR 帳號)
# ─────────────────────────────────────────────────────────────

@router.get("/admins", response_model=_List[HRAdminOut])
async def list_hr_admins(
    _hr: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """取得所有 HR 帳號列表（需登入）。"""
    result = await db.execute(select(HRAdmin).order_by(HRAdmin.created_at))
    admins = result.scalars().all()
    return admins


@router.post("/admins", response_model=HRAdminOut)
async def create_hr_admin(
    body: HRAdminCreate,
    _hr: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """新增一個 HR 帳號（需登入）。"""
    existing = await db.execute(select(HRAdmin).where(HRAdmin.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="此 Email 已存在")

    new_admin = HRAdmin(
        email=body.email,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    db.add(new_admin)
    await db.commit()
    await db.refresh(new_admin)
    return new_admin


@router.patch("/admins/{admin_id}", response_model=HRAdminOut)
async def update_hr_admin(
    admin_id: str,
    body: HRAdminUpdate,
    _hr: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """修改指定 HR 帳號的 email / 密碼 / 啟用狀態（需登入）。"""
    result = await db.execute(select(HRAdmin).where(HRAdmin.id == admin_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="HR 帳號不存在")

    if body.email is not None and body.email != admin.email:
        dup = await db.execute(select(HRAdmin).where(HRAdmin.email == body.email))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="此 Email 已被其他帳號使用")
        admin.email = body.email

    if body.password is not None:
        admin.password_hash = hash_password(body.password)

    if body.is_active is not None:
        admin.is_active = body.is_active

    await db.commit()
    await db.refresh(admin)
    return admin


@router.delete("/admins/{admin_id}")
async def delete_hr_admin(
    admin_id: str,
    _hr: str = Depends(get_current_hr),
    db: AsyncSession = Depends(get_db),
):
    """停用（軟刪除）指定 HR 帳號。無法停用自己。"""
    current_result = await db.execute(
        select(HRAdmin).where(HRAdmin.email == _hr)
    )
    current_admin = current_result.scalar_one_or_none()

    result = await db.execute(select(HRAdmin).where(HRAdmin.id == admin_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="HR 帳號不存在")

    if current_admin and str(current_admin.id) == admin_id:
        raise HTTPException(status_code=400, detail="無法停用自己的帳號")

    admin.is_active = False
    await db.commit()
    return {"ok": True}