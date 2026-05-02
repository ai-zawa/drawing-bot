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
DIFY_API_KEY_DETAIL = os.environ.get("DIFY_API_KEY_DETAIL")
DIFY_API_URL = os.environ.get("DIFY_API_URL")

# 最後に受け取った画像データをユーザーごとに一時保存する辞書
# キー：ユーザーID、値：画像データ（バイト列）
last_image_store = {}

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
            
            # テキストメッセージの場合
            if message["type"] == "text":
                user_message = message["text"].strip()
                
                # 「詳しく」と送られた場合
                if user_message == "詳しく":
                    # 保存された画像があるか確認
                    if user_id in last_image_store:
                        await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                        image_data = last_image_store[user_id]
                        analysis_result = await analyze_with_dify(image_data, mode="detail")
                        await push_message(user_id, analysis_result)
                    else:
                        await reply_message(reply_token, "先に絵の写真を送ってください📷")
                else:
                    await reply_message(reply_token, "絵の写真を送ってください📷")
            
            # 画像メッセージの場合
            elif message["type"] == "image":
                image_id = message["id"]
                
                # まず「待ってください」メッセージを送る
                await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                
                # 画像データを取得
                image_data = await get_line_image(image_id)
                
                if image_data:
                    # 画像をユーザーごとに保存しておく
                    last_image_store[user_id] = image_data
                    
                    # モードAで分析
                    analysis_result = await analyze_with_dify(image_data, mode="quick")
                    await push_message(user_id, analysis_result)
                else:
                    await push_message(user_id, "画像の取得に失敗しました。もう一度お試しください。")
    
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
            return response.content
        return None

# Difyに画像を送って分析結果を取得する関数
async def analyze_with_dify(image_data: bytes, mode: str = "quick"):
    # モードによってAPIキーを切り替える
    api_key = DIFY_API_KEY_DETAIL if mode == "detail" else DIFY_API_KEY
    
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 画像をDifyにアップロード
        files = {"file": ("image.jpg", image_data, "image/jpeg")}
        data = {"user": "line-user"}
        upload_response = await client.post(upload_url, headers=headers, files=files, data=data)
        
        if upload_response.status_code != 201:
            return "画像のアップロードに失敗しました。"
        
        file_id = upload_response.json().get("id")
        
        #
