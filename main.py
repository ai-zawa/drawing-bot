from fastapi import FastAPI, Request
import httpx
import os
import uuid
from supabase import create_client

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
DIFY_API_KEY_DETAIL = os.environ.get("DIFY_API_KEY_DETAIL")
DIFY_API_URL = os.environ.get("DIFY_API_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

last_image_store = {}

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
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, headers=headers, json=payload)
    except Exception:
        pass

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
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, headers=headers, json=payload)
    except Exception:
        pass

async def get_line_image(image_id: str):
    url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.content
            return None
    except Exception:
        return None

async def analyze_with_dify(image_data: bytes, mode: str = "quick", notes: str = None):
    api_key = DIFY_API_KEY_DETAIL if mode == "detail" else DIFY_API_KEY
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"file": ("image.jpg", image_data, "image/jpeg")}
            data = {"user": "line-user"}
            upload_response = await client.post(
                upload_url, headers=headers, files=files, data=data
            )
            
            if upload_response.status_code != 201:
                return "⚠️ 画像のアップロードに失敗しました。もう一度お試しください。"
            
            file_id = upload_response.json().get("id")
            
            run_url = f"{DIFY_API_URL}/workflows/run"
            
            # inputsにnotesを追加
            inputs = {
                "image": {
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                    "type": "image"
                }
            }
            if notes:
                inputs["notes"] = notes
            
            payload = {
                "inputs": inputs,
                "response_mode": "blocking",
                "user": "line-user"
            }
            
            run_response = await client.post(run_url, headers=headers, json=payload)
            
            if run_response.status_code == 200:
                result = run_response.json()
                text = result.get("data", {}).get("outputs", {}).get("text")
                if text:
                    return text
                return "⚠️ 分析結果を取得できませんでした。もう一度お試しください。"
            
            if run_response.status_code == 429:
                return "⚠️ 現在アクセスが集中しています。しばらく時間をおいてお試しください。"
            
            return "⚠️ 分析に失敗しました。もう一度お試しください。"
    
    except httpx.TimeoutException:
        return "⚠️ 分析に時間がかかっています。もう一度お試しください。"
    
    except Exception:
        return "⚠️ エラーが発生しました。しばらく時間をおいてお試しください。"

async def save_image(user_id: str, image_data: bytes) -> str:
    try:
        file_name = f"{user_id}/{uuid.uuid4()}.jpg"
        supabase.storage.from_("drawings").upload(
            file_name,
            image_data,
            {"content-type": "image/jpeg"}
        )
        return file_name
    except Exception as e:
        print(f"画像保存エラー: {e}")
        return None

async def save_drawing(user_id: str, image_path: str = None, analysis_a: str = None, analysis_b: str = None, notes: str = None):
    try:
        data = {
            "user_id": user_id,
            "image_path": image_path,
            "analysis_mode_a": analysis_a,
            "analysis_mode_b": analysis_b,
            "notes": notes,
        }
        supabase.table("drawings").insert(data).execute()
    except Exception as e:
        print(f"Supabase保存エラー: {e}")

# 最新レコードにメモを追記する関数
async def update_notes(user_id: str, notes: str):
    try:
        # 最新レコードのIDを取得
        result = supabase.table("drawings")\
            .select("id")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data:
            record_id = result.data[0]["id"]
            # notesを更新
            supabase.table("drawings")\
                .update({"notes": notes})\
                .eq("id", record_id)\
                .execute()
            return True
        return False
    except Exception as e:
        print(f"メモ更新エラー: {e}")
        return False

@app.get("/")
@app.head("/")
async def health_check():
    return {"status": "ok"}

@app.post("/callback")
async def callback(request: Request):
    try:
        body = await request.json()
        events = body.get("events", [])
        
        for event in events:
            if event.get("type") == "message":
                reply_token = event["replyToken"]
                message = event["message"]
                user_id = event["source"]["userId"]
                
                if message["type"] == "text":
                    user_message = message["text"].strip()
                    
                    # 「メモ：」で始まるテキストの場合
                    if user_message.startswith("メモ：") or user_message.startswith("メモ:"):
                        notes = user_message.replace("メモ：", "").replace("メモ:", "").strip()
                        success = await update_notes(user_id, notes)
                        if success:
                            await reply_message(reply_token, "📝 メモを保存しました")
                        else:
                            await reply_message(reply_token, "⚠️ 先に絵の写真を送ってください📷")
                    
                    # 「詳しく」の場合
                    elif user_message == "詳しく":
                        if user_id in last_image_store:
                            await reply_message(
                                reply_token,
                                "🎨 絵を見ています…少しだけお待ちください"
                            )
                            image_data = last_image_store[user_id]
                            
                            # 最新レコードのnotesを取得
                            notes_result = supabase.table("drawings")\
                                .select("notes")\
                                .eq("user_id", user_id)\
                                .order("created_at", desc=True)\
                                .limit(1)\
                                .execute()
                            notes = notes_result.data[0]["notes"] if notes_result.data else None
                            
                            analysis_result = await analyze_with_dify(
                                image_data, mode="detail", notes=notes
                            )
                            await save_drawing(
                                user_id,
                                analysis_b=analysis_result
                            )
                            await push_message(user_id, analysis_result)
                        else:
                            await reply_message(
                                reply_token,
                                "先に絵の写真を送ってください📷"
                            )
                    
                    else:
                        await reply_message(
                            reply_token,
                            "絵の写真を送ってください📷"
                        )
                
                elif message["type"] == "image":
                    image_id = message["id"]
                    await reply_message(
                        reply_token,
                        "🎨 絵を見ています…少しだけお待ちください"
                    )
                    
                    image_data = await get_line_image(image_id)
                    
                    if image_data:
                        last_image_store[user_id] = image_data
                        image_path = await save_image(user_id, image_data)
                        analysis_result = await analyze_with_dify(
                            image_data, mode="quick"
                        )
                        await save_drawing(
                            user_id,
                            image_path=image_path,
                            analysis_a=analysis_result
                        )
                        await push_message(user_id, analysis_result)
                        await push_message(
                            user_id,
                            "💡「詳しく」と送ると、より詳細な分析が受け取れます\n📝「メモ：〇〇」で付帯情報を追加できます"
                        )
                    else:
                        await push_message(
                            user_id,
                            "⚠️ 画像の取得に失敗しました。もう一度お試しください。"
                        )
    
    except Exception:
        pass
    
    return {"status": "ok"}
