"""
app/config.py  –  Centralised settings loaded from .env
"""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres123@localhost:5432/interview_system"


    # JWT
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # HGOOGLE_SERVICE_ACCOUNT_FILER seed account
    HR_ADMIN_EMAIL: str = "yukali58822@gmail.com"
    HR_ADMIN_PASSWORD: str = "Admin1234"

    # SMTP
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # Google
    GOOGLE_SERVICE_ACCOUNT_FILE: str = "google_service_account.json"
    GOOGLE_CALENDAR_ID: str = ""
    # GOOGLE_CREDENTIALS_FILE: str = "google_service_account.json"
    GOOGLE_TOKEN_FILE: str = "token.json"

    # Google OAuth2 (for HR login with Google account) （應該放 .env）
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # Redirect URIs
    GOOGLE_HR_REDIRECT_URI: str = "http://localhost:4459/api/hr/google-callback"
    GOOGLE_APPLICANT_REDIRECT_URI: str = "http://localhost:4459/api/applicant/google-callback"

    # App
    APP_BASE_URL: str = "http://localhost:4459"

    class Config:
        env_file = ".env"
        extra = "ignore"

class Config:
    env_file = ".env"
    env_file_encoding = "utf-8"
    extra = "ignore"


settings = Settings()


# Internal Attendees Rules (by position keywords)

DEFAULT_ATTENDEES = [
    {"name": "HR", "email": "dinglinhr@limin.tw"} # 改為HR的google帳號
]

spec_attendees = [

] 

POSITION_ATTENDEE_RULES = {
}

