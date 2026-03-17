# FastAPIというWebサーバーのフレームワークをインポート
# Requestはユーザーからの受信データを扱うためのクラス
from fastapi import FastAPI, Request

# httpxはHTTPリクエストを送るためのライブラリ
# LINEのサーバーに返信を送るときに使う
import httpx

# osはシステムの環境変数を読み込むためのライブラリ
# APIキーなどの秘密情報をコードに直接書かないために使う
import os

# FastAPIのアプリケーションを作成
app = FastAPI()

# 環境変数からLINEのトークンとシークレットを読み込む
# 実際の値はRender.comの環境変数に設定するので、ここには書かない
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# LINEから送られてくるWebhookを受け取るエンドポイント
# ユーザーがLINEでメッセージを送ると、LINEのサーバーがここにデータを転送してくる
@app.post("/callback")
async def callback(request: Request):
    
    # 受信したデータをJSON形式で取得
    body = await request.json()
    
    # イベントの一覧を取得（メッセージ送信などのアクションがイベントとして入っている）
    events = body.get("events", [])
    
    # イベントを一つずつ処理
    for event in events:
        
        # イベントの種類が「メッセージ」かどうかを確認
        if event.get("type") == "message":
            
            # 返信に必要なトークンを取得（これがないと返信できない）
            reply_token = event["replyToken"]
            
            # メッセージの内容を取得
            message = event["message"]
            
            # メッセージの種類がテキストの場合
            if message["type"] == "text":
                user_message = message["text"]
                # テキストをそのまま返信（動作確認用）
                await reply_message(reply_token, f"受信しました：{user_message}")
            
            # メッセージの種類が画像の場合
            elif message["type"] == "image":
                # 今はまだ仮の返信（後でDify連携に差し替える）
                await reply_message(reply_token, "画像を受信しました！まもなく絵の分析機能を追加します。")
    
    # LINEのサーバーに「正常に受け取った」と返す
    return {"status": "ok"}

# LINEに返信を送る関数
async def reply_message(reply_token: str, text: str):
    
    # LINEの返信APIのURL
    url = "https://api.line.me/v2/bot/message/reply"
    
    # 認証情報をヘッダーに含める
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # 返信内容を組み立てる
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    
    # LINEのサーバーに返信を送信
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=payload)
