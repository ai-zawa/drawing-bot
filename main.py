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
DIFY_API_KEY_REVIEW = os.environ.get("DIFY_API_KEY_REVIEW")
DIFY_API_KEY_INGEST = os.environ.get("DIFY_API_KEY_INGEST")

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

# 過去の分析結果を取得する関数
async def get_past_analyses(user_id: str, limit: int = 5) -> str:
    try:
        result = supabase.table("drawings")\
            .select("created_at, analysis_mode_b, analysis_mode_b_with_notes")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        
        if not result.data:
            return None
        
        analyses = []
        for i, record in enumerate(reversed(result.data), 1):
            analysis = record.get("analysis_mode_b_with_notes") or record.get("analysis_mode_b")
            if analysis:
                date = record["created_at"][:10]
                analyses.append(f"【{i}枚目 {date}】\n{analysis}")
        
        return "\n\n".join(analyses) if analyses else None
    except Exception as e:
        print(f"過去データ取得エラー: {e}")
        return None

# 振り返りワークフローを実行する関数
async def analyze_review(current_analysis: str, past_analyses: str) -> str:
    api_key = DIFY_API_KEY_REVIEW
    run_url = f"{DIFY_API_URL}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            payload = {
                "inputs": {
                    "current_analysis": current_analysis,
                    "past_analyses": past_analyses
                },
                "response_mode": "blocking",
                "user": "line-user"
            }
            
            run_response = await client.post(run_url, headers=headers, json=payload)
            
            if run_response.status_code == 200:
                result = run_response.json()
                text = result.get("data", {}).get("outputs", {}).get("text")
                if text:
                    return text
                return "⚠️ 振り返り結果を取得できませんでした。"
            
            if run_response.status_code == 429:
                return "⚠️ 現在アクセスが集中しています。しばらく時間をおいてお試しください。"
            
            return "⚠️ 振り返りに失敗しました。もう一度お試しください。"
    
    except httpx.TimeoutException:
        return "⚠️ 振り返りに時間がかかっています。もう一度お試しください。"
    
    except Exception as e:
        print(f"振り返りエラー: {e}")
        return "⚠️ エラーが発生しました。しばらく時間をおいてお試しください。"

# 振り返り結果をreviewsテーブルに保存する関数
async def save_review(user_id: str, review_text: str, drawing_ids: list):
    try:
        data = {
            "user_id": user_id,
            "review_text": review_text,
            "drawing_ids": drawing_ids
        }
        supabase.table("reviews").insert(data).execute()
    except Exception as e:
        print(f"振り返り保存エラー: {e}")

# 概念ページを取得する関数
async def get_wiki_page(user_id: str, concept: str) -> dict:
    try:
        result = supabase.table("wiki_pages")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("concept", concept)\
            .execute()
        
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"Wiki取得エラー: {e}")
        return None

# 概念ページを保存・更新する関数
async def save_wiki_page(user_id: str, concept: str, wiki_data: dict, drawing_id: str):
    try:
        existing = await get_wiki_page(user_id, concept)
        
        # source_drawing_idsを更新
        if existing:
            existing_ids = existing.get("source_drawing_ids") or []
            if drawing_id not in existing_ids:
                existing_ids.append(drawing_id)
        else:
            existing_ids = [drawing_id]
        
        data = {
            "user_id": user_id,
            "concept": concept,
            "summary": wiki_data.get("summary"),
            "schema_exploration": wiki_data.get("schema_exploration"),
            "schema_narrative": wiki_data.get("schema_narrative"),
            "schema_relationship": wiki_data.get("schema_relationship"),
            "schema_inquiry": wiki_data.get("schema_inquiry"),
            "timeline": wiki_data.get("timeline"),
            "updated_at": "now()",
            "source_drawing_ids": existing_ids
        }
        
        if existing:
            supabase.table("wiki_pages")\
                .update(data)\
                .eq("user_id", user_id)\
                .eq("concept", concept)\
                .execute()
        else:
            supabase.table("wiki_pages")\
                .insert(data)\
                .execute()
    except Exception as e:
        print(f"Wiki保存エラー: {e}")



# Ingestワークフローを実行する関数
    async def run_ingest(user_id: str, concept: str, analysis: str, notes: str, drawing_id: str):
    print(f"run_ingest開始: concept={concept}, has_notes={bool(notes)}")
    try:
        # 現在の概念ページを取得
        existing = await get_wiki_page(user_id, concept)
        current_wiki = ""
        if existing:
            import json
            current_wiki = json.dumps({
                "summary": existing.get("summary"),
                "schema_exploration": existing.get("schema_exploration"),
                "schema_narrative": existing.get("schema_narrative"),
                "schema_relationship": existing.get("schema_relationship"),
                "schema_inquiry": existing.get("schema_inquiry"),
                "timeline": existing.get("timeline")
            }, ensure_ascii=False)
        
        # 既存の概念ページ名一覧を取得
        existing_concepts = await get_existing_concepts(user_id)
        existing_concepts_str = ", ".join(existing_concepts) if existing_concepts else ""
        
        has_notes = "true" if notes else "false"
        today = __import__('datetime').date.today().isoformat()
        analysis_with_date = f"[{today}]\n{analysis}"
        
        headers = {
            "Authorization": f"Bearer {DIFY_API_KEY_INGEST}"
        }
        
        run_url = f"{DIFY_API_URL}/workflows/run"
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "inputs": {
                    "concept": concept,
                    "analysis": analysis_with_date,
                    "notes": notes or "",
                    "has_notes": has_notes,
                    "current_wiki": current_wiki,
                    "existing_concepts": existing_concepts_str,
                    "supabase_url": SUPABASE_URL,
                    "supabase_key": SUPABASE_KEY,
                    "user_id": user_id
                },
                "response_mode": "blocking",
                "user": "line-user"
            }
            
            response = await client.post(run_url, headers=headers, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                text = result.get("data", {}).get("outputs", {}).get("text", "")
                
                if text:
                    import json
                    clean = text.replace("```json", "").replace("```", "").strip()
                    wiki_data = json.loads(clean)
                    await save_wiki_page(user_id, concept, wiki_data, drawing_id)
                    print(f"Wiki保存完了: concept={concept}")
                    return True
            
            print(f"Difyエラー: status={response.status_code}")
            return False
    
    except Exception as e:
        print(f"Ingestエラー: {e}")
        return False

    
    except Exception as e:
        print(f"Ingestエラー: {e}")
        return False

# モードBの出力からモチーフタグを抽出する関数
def extract_tags(analysis_text: str) -> list:
    tags = []
    try:
        import re
        
        # ★コア概念タグ★セクションを探す
        pattern = r'★コア概念タグ★\n(.*?)(?=\n\n|\n①|\Z)'
        match = re.search(pattern, analysis_text, re.DOTALL)
        
        if match:
            items_text = match.group(1)
            for line in items_text.strip().split('\n'):
                line = line.strip()
                if '：' in line or ':' in line:
                    # 「・モチーフ：富士山」→「富士山」
                    # 「・素材：粘土 絵の具」→「粘土」「絵の具」に分割
                    tag_part = line.split('：')[-1].split(':')[-1].strip()
                    # 先頭の「・」を除去
                    tag_part = tag_part.lstrip('・').strip()
                    
                    # スペースや「、」「,」で複数タグに分割
                    sub_tags = re.split(r'[\s、,]+', tag_part)
                    for tag in sub_tags:
                        tag = tag.strip()
                        if tag and len(tag) <= 15:
                            tags.append(tag)
        
        # 旧形式（★この絵に描かれているもの★）にも対応
        if not tags:
            pattern_old = r'★この絵に描かれているもの★\n(.*?)(?=\n\n|\n①|\Z)'
            match_old = re.search(pattern_old, analysis_text, re.DOTALL)
            if match_old:
                items_text = match_old.group(1)
                for line in items_text.strip().split('\n'):
                    line = line.strip()
                    if line.startswith('・'):
                        tag = line[1:].split('（')[0].split('(')[0].strip()
                        if tag:
                            tags.append(tag)
    
    except Exception as e:
        print(f"タグ抽出エラー: {e}")
    return tags
        
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

async def update_analysis_b(user_id: str, analysis_b: str):
    try:
        result = supabase.table("drawings")\
            .select("id")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data:
            record = result.data[0]
            record_id = record["id"]
            
            # タグを抽出
            tags = extract_tags(analysis_b)
            
            update_data = {"analysis_mode_b": analysis_b}
            if tags:
                update_data["tags"] = tags
            
            supabase.table("drawings")\
                .update(update_data)\
                .eq("id", record_id)\
                .execute()
            
            # タグをもとにIngest処理を実行（付帯情報なし）
            for concept in tags:
                await run_ingest(
                    user_id=user_id,
                    concept=concept,
                    analysis=analysis_b,
                    notes="",
                    drawing_id=record_id
                )
            return True
        return False
    except Exception as e:
        print(f"モードB更新エラー: {e}")
        return False

# 最新レコードのちなみにモードB分析結果を更新する関数
async def update_analysis_b_with_notes(user_id: str, analysis_b: str):
    try:
        result = supabase.table("drawings")\
            .select("id, notes")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data:
            record = result.data[0]
            record_id = record["id"]
            notes = record.get("notes") or ""
            
            # タグを抽出
            tags = extract_tags(analysis_b)
            
            update_data = {"analysis_mode_b_with_notes": analysis_b}
            if tags:
                update_data["tags"] = tags
            
            supabase.table("drawings")\
                .update(update_data)\
                .eq("id", record_id)\
                .execute()
            
            # タグをもとにIngest処理を実行（付帯情報あり）
            for concept in tags:
                await run_ingest(
                    user_id=user_id,
                    concept=concept,
                    analysis=analysis_b,
                    notes=notes,
                    drawing_id=record_id
                )
            return True
        return False
    except Exception as e:
        print(f"ちなみにモードB更新エラー: {e}")
        return False

# 最新レコードにメモを追記する関数
async def update_notes(user_id: str, notes: str):
    try:
        result = supabase.table("drawings")\
            .select("id")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data:
            record_id = result.data[0]["id"]
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

                    # 「振り返って」の場合（振り返りモード）
                    if user_message == "振り返って":
                        await reply_message(
                            reply_token,
                            "🎨 これまでの絵を振り返っています…少しだけお待ちください"
                        )
                        
                        # 過去の分析と絵のIDを取得
                        try:
                            past_records = supabase.table("drawings")\
                                .select("id, created_at, analysis_mode_b, analysis_mode_b_with_notes")\
                                .eq("user_id", user_id)\
                                .order("created_at", desc=True)\
                                .limit(5)\
                                .execute()
                            
                            if not past_records.data:
                                await push_message(
                                    user_id,
                                    "まだ絵の記録が十分にありません。絵をもう少し送ってみてください📷"
                                )
                            else:
                                # 絵のIDを収集
                                drawing_ids = [r["id"] for r in past_records.data]
                                
                                # 過去の分析テキストを整形
                                analyses = []
                                for i, record in enumerate(reversed(past_records.data), 1):
                                    analysis = record.get("analysis_mode_b_with_notes") or record.get("analysis_mode_b")
                                    if analysis:
                                        date = record["created_at"][:10]
                                        analyses.append(f"【{i}枚目 {date}】\n{analysis}")
                                
                                past_analyses = "\n\n".join(analyses) if analyses else ""
                                
                                # 最新の分析を取得
                                current_analysis = analyses[-1] if analyses else ""
                                
                                review_result = await analyze_review(current_analysis, past_analyses)
                                
                                # エラーでなければ保存
                                if not review_result.startswith("⚠️"):
                                    await save_review(user_id, review_result, drawing_ids)
                                
                                await push_message(user_id, review_result)
                        
                        except Exception as e:
                            print(f"振り返りエラー: {e}")
                            await push_message(user_id, "⚠️ 振り返りに失敗しました。もう一度お試しください。")

                    # 「ちなみに」で始まるテキストの場合（付帯情報つきモードB）
                    if user_message.startswith("ちなみに"):
                        if user_id in last_image_store:
                            notes = user_message.replace("ちなみに", "").strip()
                            await update_notes(user_id, notes)
                            await reply_message(
                                reply_token,
                                "🎨 絵を見ています…少しだけお待ちください"
                            )
                            image_data = last_image_store[user_id]
                            analysis_result = await analyze_with_dify(
                                image_data, mode="detail", notes=notes
                            )
                            # エラーの場合は保存しない
                            if not analysis_result.startswith("⚠️"):
                                await update_analysis_b_with_notes(user_id, analysis_result)
                            await push_message(user_id, analysis_result)
                        else:
                            await reply_message(
                                reply_token,
                                "先に絵の写真を送ってください📷"
                            )
                    
                    # 「詳しく」の場合（付帯情報なしモードB）
                    elif user_message == "詳しく":
                        if user_id in last_image_store:
                            await reply_message(
                                reply_token,
                                "🎨 絵を見ています…少しだけお待ちください"
                            )
                            image_data = last_image_store[user_id]
                            analysis_result = await analyze_with_dify(
                                image_data, mode="detail"
                            )
                            # エラーの場合は保存しない
                            if not analysis_result.startswith("⚠️"):
                                await update_analysis_b(user_id, analysis_result)
                            await push_message(user_id, analysis_result)
                        else:
                            await reply_message(
                                reply_token,
                                "先に絵の写真を送ってください📷"
                            )
                
                elif message["type"] == "image":
                    image_id = message["id"]
                    await reply_message(
                        reply_token,
                        "🎨 絵を見ています…少しだけお待ちください"
                    )
                    
                    image_data = await get_line_image(image_id)
                    
                    if image_data:
                        # まず分析を実行
                        analysis_result = await analyze_with_dify(
                            image_data, mode="quick"
                        )
                        
                        # エラーの場合は保存せず終了
                        if analysis_result.startswith("⚠️"):
                            await push_message(user_id, analysis_result)
                        else:
                            # 成功した場合のみ保存
                            last_image_store[user_id] = image_data
                            image_path = await save_image(user_id, image_data)
                            await save_drawing(
                                user_id,
                                image_path=image_path,
                                analysis_a=analysis_result
                            )
                            await push_message(user_id, analysis_result)
                            await push_message(
                            user_id,
                            "💡「詳しく」→ より詳細な分析\n💡「ちなみに〇〇」→ 付帯情報を加えた詳細分析\n💡「振り返って」→ これまでの絵を振り返る"
                            )
                    else:
                        await push_message(
                            user_id,
                            "⚠️ 画像の取得に失敗しました。もう一度お試しください。"
                        )
    
    except Exception:
        pass
    
    return {"status": "ok"}
