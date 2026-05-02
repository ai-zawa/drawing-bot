from fastapi import FastAPI, Request
import httpx
import os

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
DIFY_API_KEY_DETAIL = os.environ.get("DIFY_API_KEY_DETAIL")
DIFY_API_URL = os.environ.get("DIFY_API_URL")

last_image_store = {}

# LINEに返信を送る関数（callbackより前に定義）
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

# LINEにプッシュ通知を送る関数（callbackより前に定義）
async def push_message(user_id: str, text: str):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}]
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=payload)

# LINEから画像データを取得する関数
async def get_line_image(image_id: str):
    url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            return response.content
        return None

# Difyに画像を送って分析結果を取得する関数
async def analyze_with_dify(image_data: bytes, mode: str = "quick"):
    api_key = DIFY_API_KEY_DETAIL if mode == "detail" else DIFY_API_KEY
    
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": ("image.jpg", image_data, "image/jpeg")}
        data = {"user": "line-user"}
        upload_response = await client.post(upload_url, headers=headers, files=files, data=data)
        
        if upload_response.status_code != 201:
            return "画像のアップロードに失敗しました。"
        
        file_id = upload_response.json().get("id")
        
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
            result = run_response.json()
            return result.get("data", {}).get("outputs", {}).get("text", "分析結果を取得できませんでした。")
        
        return "分析に失敗しました。もう一度お試しください。"

# LINEから送られてくるWebhookを受け取るエンドポイント
@app.post("/callback")
async def callback(request: Request):
    body = await request.json()
    events = body.get("events", [])
    
    for event in events:
        if event.get("type") == "message":
            reply_token = event["replyToken"]
            message = event["message"]
            user_id = event["source"]["userId"]
            
            if message["type"] == "text":
                user_message = message["text"].strip()
                
                if user_message == "詳しく":
                    if user_id in last_image_store:
                        await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                        image_data = last_image_store[user_id]
                        analysis_result = await analyze_with_dify(image_data, mode="detail")
                        await push_message(user_id, analysis_result)
                    else:
                        await reply_message(reply_token, "先に絵の写真を送ってください📷")
                else:
                    await reply_message(reply_token, "絵の写真を送ってください📷")
            
            elif message["type"] == "image":
                image_id = message["id"]
                await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                
                image_data = await get_line_image(image_id)
                
                if image_data:
                    last_image_store[user_id] = image_data
                    analysis_result = await analyze_with_dify(image_data, mode="quick")
                    await push_message(user_id, analysis_result)
                else:
                    await push_message(user_id, "画像の取得に失敗しました。もう一度お試しください。")
    
    return {"status": "ok"}
