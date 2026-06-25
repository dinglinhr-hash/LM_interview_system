#!/bin/sh
# entrypoint.sh
# 啟動前的準備工作 + 啟動 uvicorn，動態讀取 Zeabur（或任何平台）
# 透過環境變數 PORT 注入的監聽埠號，避免 port 寫死跟平台實際轉發的埠號不一致而出現 502。

set -e

# 如果有提供 GOOGLE_SERVICE_ACCOUNT_JSON（完整的 service account JSON 內容），
# 啟動前先寫成檔案，這樣這份機密就不需要進版控或打包進 image。
# 用 printf '%s' 而不是 echo，避免 shell 把 JSON 裡的 \n（例如 private_key 裡的換行）
# 誤判成跳脫字元而展開錯誤。
if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
  printf '%s' "$GOOGLE_SERVICE_ACCOUNT_JSON" > /app/google_service_account.json
fi

# Zeabur／大多數雲端平台會用 PORT 環境變數告訴容器要監聽哪個埠號。
# 若沒有提供（例如純本機 docker run 沒設這個變數），預設用 5000。
PORT="${PORT:-5000}"

echo "Starting uvicorn on port ${PORT}..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
