#!/usr/bin/env python3
"""
Google Calendar Integration Verification Script
快速驗證Google Calendar整合是否正確配置
"""

import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime, date, time

def check_environment():
    """檢查環境和依賴"""
    print("\n" + "="*60)
    print("📋 環境檢查")
    print("="*60)
    
    try:
        import google.auth
        print("✅ google-auth 已安裝")
    except ImportError:
        print("❌ google-auth 未安裝: pip install google-auth")
        return False
    
    try:
        from googleapiclient import discovery
        print("✅ google-api-python-client 已安裝")
    except ImportError:
        print("❌ google-api-python-client 未安裝: pip install google-api-python-client")
        return False
    
    try:
        import sqlalchemy
        print("✅ sqlalchemy 已安裝")
    except ImportError:
        print("❌ sqlalchemy 未安裝: pip install sqlalchemy")
        return False
    
    return True


def check_config():
    """檢查應用配置"""
    print("\n" + "="*60)
    print("⚙️  配置檢查")
    print("="*60)
    
    from app.config import settings
    
    # 檢查Google Calendar ID
    if settings.GOOGLE_CALENDAR_ID:
        print(f"✅ GOOGLE_CALENDAR_ID: {settings.GOOGLE_CALENDAR_ID}")
    else:
        print("⚠️  GOOGLE_CALENDAR_ID 未設定 (可選)")
    
    # 檢查Service Account文件
    if hasattr(settings, 'GOOGLE_SERVICE_ACCOUNT_FILE'):
        file_path = settings.GOOGLE_SERVICE_ACCOUNT_FILE
        if Path(file_path).exists():
            print(f"✅ Service Account 文件存在: {file_path}")
            try:
                with open(file_path) as f:
                    data = json.load(f)
                    if data.get('type') == 'service_account':
                        print(f"   ✅ 類型: service_account")
                        print(f"   ✅ Project ID: {data.get('project_id')}")
                        print(f"   ✅ Client Email: {data.get('client_email')}")
                        return True
                    else:
                        print(f"❌ 無效的Service Account文件類型")
                        return False
            except json.JSONDecodeError:
                print(f"❌ Service Account JSON格式無效")
                return False
        else:
            print(f"❌ Service Account 文件不存在: {file_path}")
            return False
    else:
        print("❌ GOOGLE_SERVICE_ACCOUNT_FILE 未配置")
        return False


def check_imports():
    """檢查模組導入"""
    print("\n" + "="*60)
    print("📦 模組導入檢查")
    print("="*60)
    
    try:
        from app.services import google_calendar
        print("✅ google_calendar 模組導入成功")
    except ImportError as e:
        print(f"❌ google_calendar 導入失敗: {e}")
        return False
    
    try:
        from app.routers import bookings
        print("✅ bookings 路由導入成功")
    except ImportError as e:
        print(f"❌ bookings 導入失敗: {e}")
        return False
    
    try:
        from app.models.models import Booking, BookingStatus
        print("✅ Booking 模型導入成功")
        print(f"   ✅ 可用狀態: {[s.value for s in BookingStatus]}")
    except ImportError as e:
        print(f"❌ Booking 模型導入失敗: {e}")
        return False
    
    return True


def check_database():
    """檢查數據庫"""
    print("\n" + "="*60)
    print("💾 數據庫檢查")
    print("="*60)
    
    try:
        import sqlite3
        from app.models.models import Booking
        print("✅ SQLAlchemy ORM 正確配置")
        print(f"   ✅ Booking 模型字段:")
        for col in Booking.__table__.columns:
            print(f"      - {col.name}: {col.type}")
        return True
    except Exception as e:
        print(f"❌ 數據庫檢查失敗: {e}")
        return False


def check_google_service():
    """檢查Google服務連接"""
    print("\n" + "="*60)
    print("🌐 Google服務連接檢查")
    print("="*60)
    
    try:
        from app.services.google_calendar import _build_service, _build_gmail_service
        from app.config import settings
        
        # 檢查Calendar服務
        service = _build_service()
        if service:
            print("✅ Google Calendar API 連接成功")
            # 嘗試列出日曆
            try:
                calendars = service.calendarList().list().execute()
                print(f"   ✅ 可用日曆數: {len(calendars.get('items', []))}")
            except Exception as e:
                print(f"   ⚠️  無法列出日曆: {e}")
        else:
            print("❌ Google Calendar API 連接失敗")
            print("   原因: Service Account未正確配置")
            return False
        
        # 檢查Gmail服務
        gmail = _build_gmail_service()
        if gmail:
            print("✅ Gmail API 連接成功")
        else:
            print("⚠️  Gmail API 未連接 (可選)")
        
        return True
    except Exception as e:
        print(f"❌ Google服務檢查失敗: {e}")
        return False


def check_api_endpoints():
    """檢查API端點"""
    print("\n" + "="*60)
    print("🔌 API端點檢查")
    print("="*60)
    
    try:
        from fastapi import FastAPI
        from app.main import app
        
        # 獲取所有路由
        routes = []
        for route in app.routes:
            if hasattr(route, 'path') and 'booking' in route.path.lower():
                routes.append(f"{route.methods or {'GET'}} {route.path}")
        
        if routes:
            print(f"✅ 找到 {len(routes)} 個booking相關端點:")
            for route in sorted(routes):
                print(f"   ✓ {route}")
            return True
        else:
            print("❌ 未找到booking端點")
            return False
    except Exception as e:
        print(f"❌ API端點檢查失敗: {e}")
        return False


def check_ics_functions():
    """檢查ICS相關函數"""
    print("\n" + "="*60)
    print("📨 ICS函數檢查")
    print("="*60)
    
    try:
        from app.services.google_calendar import (
            _build_ics_content,
            _send_ical_email,
        )
        
        print("✅ _build_ics_content() 函數存在")
        
        # 測試ICS構建
        test_ics = _build_ics_content(
            uid="test-123",
            summary="Test Event",
            slot_date=date.today(),
            start_time=time(14, 0),
            end_time=time(15, 0),
            organizer_email="test@example.com",
            attendee_emails=["attendee@example.com"],
            description="Test description",
            meet_link="https://meet.google.com/test",
            method="REQUEST",
            sequence=0
        )
        
        if "BEGIN:VCALENDAR" in test_ics and "END:VCALENDAR" in test_ics:
            print("✅ ICS生成成功 (RFC 5545格式)")
            lines = test_ics.split('\n')
            print(f"   - ICS行數: {len(lines)}")
            print(f"   - 包含METHOD: {'METHOD:REQUEST' in test_ics}")
            print(f"   - 包含SEQUENCE: {'SEQUENCE:' in test_ics}")
            print(f"   - 包含ATTENDEE: {'ATTENDEE:' in test_ics}")
            print(f"   - 包含Google Meet: {'https://meet.google.com' in test_ics}")
        else:
            print("❌ ICS生成失敗")
            return False
        
        print("✅ _send_ical_email() 函數存在")
        return True
    except Exception as e:
        print(f"❌ ICS函數檢查失敗: {e}")
        return False


def main():
    """主檢查流程"""
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + " Google Calendar 整合驗證腳本 ".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    checks = [
        ("環境檢查", check_environment),
        ("配置檢查", check_config),
        ("模組導入", check_imports),
        ("數據庫", check_database),
        ("Google服務", check_google_service),
        ("API端點", check_api_endpoints),
        ("ICS函數", check_ics_functions),
    ]
    
    results = {}
    for check_name, check_func in checks:
        try:
            results[check_name] = check_func()
        except Exception as e:
            print(f"\n❌ {check_name} 檢查出錯: {e}")
            results[check_name] = False
    
    # 摘要
    print("\n" + "="*60)
    print("📊 檢查摘要")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for check_name, passed_flag in results.items():
        status = "✅" if passed_flag else "❌"
        print(f"{status} {check_name}")
    
    print("\n" + "-"*60)
    print(f"通過: {passed}/{total} 個檢查")
    
    if passed == total:
        print("\n🎉 所有檢查通過！系統已準備就緒。")
        print("\n後續步驟:")
        print("1. 啟動應用: python -m uvicorn main:app --reload")
        print("2. 測試API: http://localhost:8000/docs")
        print("3. 建立測試預約: POST /api/bookings")
        print("4. 檢查Gmail收件: 驗證ICS邀約信已發送")
        print("5. 檢查Google日曆: 驗證事件已建立")
        return 0
    else:
        print("\n⚠️  部分檢查未通過。請查看上方詳細信息。")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n終止檢查。")
        sys.exit(1)
