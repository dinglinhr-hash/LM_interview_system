# expire_hr_token.py
"""
小工具：把指定 HR email 在 events.db 裡的 OAuth access_token 過期時間
強制改成過去的時間，用來手動測試「token 過期後自動 refresh」的流程是否正常。

使用方式：
    python expire_hr_token.py dinglinhr@limin.tw

不帶參數執行則預設使用下面 TARGET_EMAIL 的值。
"""
import sqlite3
import sys

TARGET_EMAIL = "dinglinhr@limin.tw"  # ← 沒帶參數時的預設值，可自行修改


def main():
    email = sys.argv[1] if len(sys.argv) > 1 else TARGET_EMAIL

    conn = sqlite3.connect("events.db")
    cur = conn.cursor()

    cur.execute("SELECT email, expires_at FROM hr_oauth_tokens WHERE email = ?", (email,))
    row = cur.fetchone()
    if not row:
        print(f"找不到 email = {email} 的 token 紀錄。")
        conn.close()
        return

    print(f"修改前：{row[0]} 的 expires_at = {row[1]}")

    cur.execute(
        "UPDATE hr_oauth_tokens SET expires_at = '2020-01-01T00:00:00' WHERE email = ?",
        (email,),
    )
    conn.commit()
    print(f"已更新 {cur.rowcount} 筆紀錄，expires_at 設為 2020-01-01T00:00:00（已過期）。")

    cur.execute("SELECT email, expires_at FROM hr_oauth_tokens WHERE email = ?", (email,))
    row = cur.fetchone()
    print(f"修改後確認：{row[0]} 的 expires_at = {row[1]}")

    conn.close()


if __name__ == "__main__":
    main()
