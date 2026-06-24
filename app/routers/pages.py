"""
app/routers/pages.py  –  HTML page routes
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import FileSystemLoader, Environment
from app.config import settings
import os

router = APIRouter(tags=["Pages"])

# Initialize Jinja2 environment with FileSystemLoader
template_dir = os.path.join(os.path.dirname(__file__), '../../templates')
jinja_env = Environment(loader=FileSystemLoader(template_dir))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    template = jinja_env.get_template("index.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)


@router.get("/hr/login", response_class=HTMLResponse)
async def hr_login_page(request: Request):
    template = jinja_env.get_template("hr_login.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)


@router.get("/hr", response_class=HTMLResponse)
async def hr_dashboard(request: Request):
    template = jinja_env.get_template("hr_dashboard.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)


@router.get("/hr/bookings", response_class=HTMLResponse)
async def hr_bookings(request: Request):
    template = jinja_env.get_template("hr_bookings.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)

@router.get("/hr/account", response_class=HTMLResponse)
async def account_settings_page(request: Request):
    template = jinja_env.get_template("account_settings.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)


@router.get("/hr/bookings/history", response_class=HTMLResponse)
async def hr_booking_history_page(request: Request):
    template = jinja_env.get_template("hr_booking_history.html")
    return template.render(request=request, base_url=settings.APP_BASE_URL)