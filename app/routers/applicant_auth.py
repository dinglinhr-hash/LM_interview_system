"""
app/routers/applicant_auth.py – Google OAuth2 authentication for applicants
"""
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import RedirectResponse
from app.services import google_oauth
from app.config import settings

router = APIRouter(prefix="/api/applicant", tags=["Applicant Auth"])


@router.get("/google-login")
async def applicant_google_login():
    """Redirect applicant to Google OAuth2 authorization page."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth2 is not configured"
        )
    
    auth_url = await google_oauth.get_google_authorization_url(
        redirect_uri=settings.GOOGLE_APPLICANT_REDIRECT_URI
    )
    return RedirectResponse(url=auth_url)


@router.get("/google-callback")
async def applicant_google_callback(
    code: str = Query(None),
    error: str = Query(None),
    state: str = Query(None),
):
    """Handle Google OAuth2 callback for applicants."""
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
    
    # Exchange code for token
    token_data = await google_oauth.exchange_code_for_token(
        code=code,
        redirect_uri=settings.GOOGLE_APPLICANT_REDIRECT_URI
    )
    if not token_data:
        raise HTTPException(
            status_code=400,
            detail="Failed to exchange authorization code for token"
        )
    
    access_token = token_data.get("access_token")
    
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="No access token in response"
        )
    
    # Get user info
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
    
    # Redirect back to main page with email and name pre-filled
    # Use URL parameters to pass the data to the frontend
    redirect_url = f"/?google_email={email}&google_name={name}"
    return RedirectResponse(url=redirect_url)
