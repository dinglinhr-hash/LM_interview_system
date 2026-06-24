"""
app/services/google_oauth.py – Google OAuth2 authentication for HR login
"""
from typing import Optional, Dict, Any
import httpx
from httpx import AsyncClient
from app.config import settings


async def refresh_access_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Exchange a stored refresh_token for a new access_token.
    Returns: {access_token, expires_in, token_type, ...} or None
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise ValueError("Google OAuth2 credentials not configured")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    async with AsyncClient() as client:
        try:
            response = await client.post(token_url, data=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error refreshing access token: {e}")
            return None


def refresh_access_token_sync(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    refresh_access_token 的同步版本。

    用途：google_calendar_manager.py 裡的 authenticate()/_get_user_credentials()
    是同步函式，但常常是從 FastAPI 的 async request handler 裡被呼叫的——
    這代表呼叫當下，目前的 thread 已經有一個事件迴圈在跑了。
    如果在這種情況下用 asyncio.run() 或 loop.run_until_complete() 去跑
    refresh_access_token()（async 版本），會直接撞上
    「Cannot run the event loop while another loop is running」，
    導致 refresh 失敗、token 永遠拿不到新的，後續建立 Calendar 行程跟著失敗。

    這裡改用同步的 httpx.Client，完全不碰事件迴圈，
    不管是不是在 async context 裡呼叫都能正常運作。
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise ValueError("Google OAuth2 credentials not configured")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        with httpx.Client() as client:
            response = client.post(token_url, data=data)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"Error refreshing access token (sync): {e}")
        return None


async def get_google_authorization_url(redirect_uri: str = None, include_calendar_scope: bool = False) -> str:
    """
    Generate Google OAuth2 authorization URL.
    include_calendar_scope=True 只應該用在 HR 登入流程，
    應徵者登入絕對不能要求 Calendar 權限。
    """
    if not redirect_uri:
        redirect_uri = settings.GOOGLE_HR_REDIRECT_URI
    
    scopes = [
        "openid",
        "email",
        "profile",
    ]
    if include_calendar_scope:
        scopes.append("https://www.googleapis.com/auth/calendar")
    scope_str = " ".join(scopes)
    
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope_str,
        "access_type": "offline",
        "prompt": "consent",
    }
    
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query_string}"


async def exchange_code_for_token(code: str, redirect_uri: str = None) -> Optional[Dict[str, Any]]:
    """
    Exchange authorization code for access token.
    Returns: {access_token, id_token, expires_in, token_type, ...} or None
    """
    if not redirect_uri:
        redirect_uri = settings.GOOGLE_HR_REDIRECT_URI
    
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise ValueError("Google OAuth2 credentials not configured")
    
    token_url = "https://oauth2.googleapis.com/token"
    
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with AsyncClient() as client:
        try:
            response = await client.post(token_url, data=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error exchanging code for token: {e}")
            return None


async def get_user_info(access_token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch user info from Google using access token.
    Returns: {sub, email, email_verified, name, picture, ...} or None
    """
    userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
    }
    
    async with AsyncClient() as client:
        try:
            response = await client.get(userinfo_url, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching user info: {e}")
            return None


async def verify_google_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Google ID token and extract user claims.
    Returns user claims if valid, None otherwise.
    """
    verify_url = "https://oauth2.googleapis.com/tokeninfo"
    
    params = {
        "id_token": token,
    }
    
    async with AsyncClient() as client:
        try:
            response = await client.post(verify_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Verify the token is for our app
            if data.get("aud") != settings.GOOGLE_CLIENT_ID:
                return None
            
            return data
        except Exception as e:
            print(f"Error verifying token: {e}")
            return None


async def get_user_profile(access_token: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed user profile info.
    Returns: {email, name, picture, locale, ...} or None
    """
    return await get_user_info(access_token)