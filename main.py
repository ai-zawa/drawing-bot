# FastAPIというWebサーバーのフレームワークをインポート
from fastapi import FastAPI, Request
# HTTPリクエストを送るためのライブラリ
import httpx
# 環境変数を読み込むためのライブラリ
import os

app = FastAPI()

# 環境変数から各種キーを読み込む
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
DIFY_API_URL = os.environ.get("DIFY_API_URL")

# LINEから送られてくるWebhookを受け取るエンドポイント
@app.post("/callback")
async def callback(request: Request):
    body = await request.json()
    events = body.get("events", [])
    
    for event in events:
        if event.get("type") == "message":
            reply_token = event["replyToken"]
            message = event["message"]
            
            # テキストメッセージの場合
            if message["type"] == "text":
                user_message = message["text"]
                await reply_message(reply_token, f"受信しました：{user_message}")
            
            # 画像メッセージの場合
            elif message["type"] == "image":
                # LINEから画像データを取得する
                image_id = message["id"]
                image_data = await get_line_image(image_id)
                
                if image_data:
                    # Difyに画像を送って分析結果を取得する
                    analysis_result = await analyze_with_dify(image_data)
                    await reply_message(reply_token, analysis_result)
                else:
                    await reply_message(reply_token, "画像の取得に失敗しました。もう一度お試しください。")
    
    return {"status": "ok"}

# LINEから画像データを取得する関数
async def get_line_image(image_id: str):
    url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            # 画像データをバイト列として返す
            return response.content
        return None

# Difyに画像を送って分析結果を取得する関数
async def analyze_with_dify(image_data: bytes):
    # まずDifyに画像をアップロードする
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 画像をDifyにアップロード
        files = {"file": ("image.jpg", image_data, "image/jpeg")}
        data = {"user": "line-user"}
        upload_response = await client.post(upload_url, headers=headers, files=files, data=data)
        
        if upload_response.status_code != 201:
            return "画像のアップロードに失敗しました。"
        
        # アップロードした画像のIDを取得
        file_id = upload_response.json().get("id")
        
        # Difyのワークフローを実行する
        run_url = f"{DIFY_API_URL}/workflows/run"
        payload = {
            "inputs": {
                "image": {
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                    "type": "image"
                }
            },
            "response_mode": "blocking",
            "user": "line-user"
        }
        
        run_response = await client.post(run_url, headers=headers, json=payload)
        
        if run_response.status_code == 200:
            # 分析結果のテキストを取得して返す
            result = run_response.json()
            return result.get("data", {}).get("outputs", {}).get("text", "分析結果を取得できませんでした。")
        
        return "分析に失敗しました。もう一度お試しください。"

# LINEに返信を送る関数
async def reply_message(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=payload)
