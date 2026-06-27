from fastapi import FastAPI, Request, BackgroundTasks
import httpx
import os
import uuid
import asyncio
from datetime import datetime, timezone
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
    except Exception as e:
        print(f"❌ LINE返信エラー: {e}")

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
    except Exception as e:
        print(f"❌ LINEプッシュエラー: {e}")

async def get_line_image(image_id: str):
    url = f"https://api-data.line.me/v2/bot/message/{image_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.content
            return None
    except Exception as e:
        print(f"❌ LINE画像取得エラー: {e}")
        return None

async def analyze_with_dify(image_data: bytes, mode: str = "quick", notes: str = None, wiki_context: str = None):
    api_key = DIFY_API_KEY_DETAIL if mode == "detail" else DIFY_API_KEY
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"file": ("image.jpg", image_data, "image/jpeg")}
            data = {"user": "line-user"}
            upload_response = await client.post(upload_url, headers=headers, files=files, data=data)
            if upload_response.status_code != 201:
                return "⚠️ 画像のアップロードに失敗しました。もう一度お試しください。"
            file_id = upload_response.json().get("id")
            run_url = f"{DIFY_API_URL}/workflows/run"
            inputs = {"image": {"transfer_method": "local_file", "upload_file_id": file_id, "type": "image"}}
            if notes:
                inputs["notes"] = notes
            if wiki_context:
                inputs["wiki_context"] = wiki_context
            payload = {"inputs": inputs, "response_mode": "blocking", "user": "line-user"}
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
    except Exception as e:
        print(f"❌ Dify分析エラー: {e}")
        return "⚠️ エラーが発生しました。しばらく時間をおいてお試しください。"

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
        print(f"❌ 過去データ取得エラー: {e}")
        return None

async def analyze_review(current_analysis: str, past_analyses: str) -> str:
    api_key = DIFY_API_KEY_REVIEW
    run_url = f"{DIFY_API_URL}/workflows/run"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            payload = {
                "inputs": {"current_analysis": current_analysis, "past_analyses": past_analyses},
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
        print(f"❌ 振り返りエラー: {e}")
        return "⚠️ エラーが発生しました。しばらく時間をおいてお試しください。"

async def save_review(user_id: str, review_text: str, drawing_ids: list):
    try:
        data = {"user_id": user_id, "review_text": review_text, "drawing_ids": drawing_ids}
        supabase.table("reviews").insert(data).execute()
    except Exception as e:
        print(f"❌ 振り返り保存エラー: {e}")

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
        print(f"❌ Wiki取得エラー: {e}")
        return None

async def get_existing_concepts(user_id: str) -> list:
    try:
        result = supabase.table("wiki_pages")\
            .select("concept")\
            .eq("user_id", user_id)\
            .execute()
        return [r["concept"] for r in result.data] if result.data else []
    except Exception as e:
        print(f"❌ 概念一覧取得エラー: {e}")
        return []

# タグに対応するwiki_pagesを取得する関数
async def get_wiki_context(user_id: str, tags: list) -> str:
    try:
        if not tags:
            return ""
        
        result = supabase.table("wiki_pages")\
            .select("concept, summary")\
            .eq("user_id", user_id)\
            .in_("concept", tags)\
            .execute()
        
        if not result.data:
            return ""
        
        context_parts = []
        for page in result.data:
            concept = page.get("concept")
            summary = page.get("summary")
            if concept and summary:
                context_parts.append(f"【{concept}】\n{summary}")
        
        return "\n\n".join(context_parts) if context_parts else ""
    except Exception as e:
        print(f"❌ Wiki context取得エラー: {e}")
        return ""

# 全概念ページのsummaryを取得する関数（Aモード用）
async def get_all_wiki_summaries(user_id: str) -> str:
    try:
        result = supabase.table("wiki_pages")\
            .select("concept, summary")\
            .eq("user_id", user_id)\
            .execute()
        
        if not result.data:
            return ""
        
        context_parts = []
        for page in result.data:
            concept = page.get("concept")
            summary = page.get("summary")
            if concept and summary:
                context_parts.append(f"【{concept}】\n{summary}")
        
        return "\n\n".join(context_parts) if context_parts else ""
    except Exception as e:
        print(f"❌ 全Wiki取得エラー: {e}")
        return ""

async def save_wiki_page(user_id: str, wiki_data: dict, drawing_id: str, concept: str):
    try:
        if not concept:
            print(f"conceptが空のためスキップ")
            return
        
        existing = await get_wiki_page(user_id, concept)
        
        # タイムラインの結合（過去を保持して新規を追加）
        timeline = []
        if existing and existing.get("timeline"):
            timeline = existing.get("timeline")
            if isinstance(timeline, str):
                import json
                timeline = json.loads(timeline)
        new_entry = wiki_data.get("new_timeline_entry")
        if new_entry:
            timeline.insert(0, new_entry)  # 最新を先頭に
        
        # 各スキーマ配列の結合（重複排除・順序維持）
        def merge_list(field_name, new_field_name):
            old_list = (existing.get(field_name) or []) if existing else []
            new_list = wiki_data.get(new_field_name) or []
            combined = list(old_list)
            for item in new_list:
                if item not in combined:
                    combined.append(item)
            return combined
        
        exploration = merge_list("schema_exploration", "new_exploration")
        narrative = merge_list("schema_narrative", "new_narrative")
        relationship = merge_list("schema_relationship", "new_relationship")
        inquiry = merge_list("schema_inquiry", "new_inquiry")
        
        # source_drawing_idsの更新
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
            "schema_exploration": exploration,
            "schema_narrative": narrative,
            "schema_relationship": relationship,
            "schema_inquiry": inquiry,
            "timeline": timeline,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source_drawing_ids": existing_ids
        }
        
        if existing:
            supabase.table("wiki_pages").update(data).eq("user_id", user_id).eq("concept", concept).execute()
        else:
            supabase.table("wiki_pages").insert(data).execute()
        
        print(f"Wiki保存（差分マージ）完了: concept={concept}")
    except Exception as e:
        print(f"❌ Wiki保存エラー: {e}")

async def run_ingest(user_id: str, concept: str, analysis: str, notes: str, drawing_id: str, max_retries: int = 2):
    print(f"run_ingest開始: concept={concept}, has_notes={bool(notes)}")
    
    existing_concepts = await get_existing_concepts(user_id)
    existing_concepts_str = ", ".join(existing_concepts) if existing_concepts else ""
    print(f"既存概念一覧: {existing_concepts_str}")
    
    has_notes = "true" if notes else "false"
    today = datetime.now(timezone.utc).date().isoformat()
    analysis_with_date = f"[{today}]\n{analysis}"
    
    headers = {"Authorization": f"Bearer {DIFY_API_KEY_INGEST}"}
    run_url = f"{DIFY_API_URL}/workflows/run"
    
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                payload = {
                    "inputs": {
                        "concept": concept,
                        "analysis": analysis_with_date,
                        "notes": notes or "",
                        "has_notes": has_notes,
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
                    outputs = result.get("data", {}).get("outputs", {})
                    
                    text = ""
                    if "text" in outputs:
                        text = outputs["text"]
                    elif "output" in outputs:
                        output_list = outputs["output"]
                        if isinstance(output_list, list) and len(output_list) > 0:
                            text = output_list[0]
                    
                    if text:
                        import json
                        clean = text.replace("```json", "").replace("```", "").strip()
                        wiki_data = json.loads(clean)
                        await save_wiki_page(user_id, wiki_data, drawing_id, concept)
                        return True
                    else:
                        print(f"textが空: outputs={outputs}")
                        if attempt < max_retries:
                            print(f"リトライします（{attempt + 1}回目）")
                            await asyncio.sleep(3)
                            continue
                        return False
                
                if response.status_code in (503, 504, 429):
                    print(f"リトライ対象エラー(status={response.status_code})。{attempt + 1}回目")
                    if attempt < max_retries:
                        await asyncio.sleep(3)
                        continue
                
                print(f"Difyエラー: status={response.status_code}")
                return False
        
        except Exception as e:
            print(f"❌ Ingestエラー: {e}")
            if attempt < max_retries:
                await asyncio.sleep(3)
                continue
            return False
    
    return False


async def ingest_all_concepts(user_id: str, tags: list, analysis: str, notes: str, record_id: str):
    for concept in tags:
        await run_ingest(user_id=user_id, concept=concept, analysis=analysis, notes=notes, drawing_id=record_id)
        await asyncio.sleep(1)

def extract_tags(analysis_text: str) -> list:
    tags = []
    try:
        import re
        pattern = r'★コア概念タグ★\n(.*?)(?=\n\n|\n---|\n①|\Z)'
        match = re.search(pattern, analysis_text, re.DOTALL)
        if match:
            items_text = match.group(1)
            for line in items_text.strip().split('\n'):
                line = line.strip()
                if '：' in line or ':' in line:
                    tag_part = line.split('：')[-1].split(':')[-1].strip()
                    tag_part = tag_part.lstrip('・').strip()
                    sub_tags = re.split(r'[\s、,]+', tag_part)
                    for tag in sub_tags:
                        tag = tag.strip()
                        if tag and len(tag) <= 15:
                            tags.append(tag)
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
        print(f"❌ タグ抽出エラー: {e}")
    return tags


# 「詳しく」「ちなみに」の重い処理をまとめてバックグラウンドで実行する関数
async def handle_detail_command(user_id: str, image_data: bytes, notes: str = None):
    try:
        # 1回目：Ingest用（wiki_contextなし）
        analysis_for_ingest = await analyze_with_dify(image_data, mode="detail", notes=notes)
        
        if analysis_for_ingest.startswith("⚠️"):
            await push_message(user_id, analysis_for_ingest)
            return
        
        tags = extract_tags(analysis_for_ingest)
        wiki_context = await get_wiki_context(user_id, tags)
        
        # 2回目：親向け（wiki_contextあり）
        if wiki_context:
            analysis_for_parent = await analyze_with_dify(image_data, mode="detail", notes=notes, wiki_context=wiki_context)
        else:
            analysis_for_parent = analysis_for_ingest
        
        if notes:
            await update_analysis_b_with_notes(user_id, analysis_for_ingest)
        else:
            await update_analysis_b(user_id, analysis_for_ingest)
        
        await push_message(user_id, analysis_for_parent)
    except Exception as e:
        print(f"❌ handle_detail_commandエラー: {e}")
        await push_message(user_id, "⚠️ 処理中にエラーが発生しました。もう一度お試しください。")


async def save_image(user_id: str, image_data: bytes) -> str:
    try:
        file_name = f"{user_id}/{uuid.uuid4()}.jpg"
        supabase.storage.from_("drawings").upload(file_name, image_data, {"content-type": "image/jpeg"})
        return file_name
    except Exception as e:
        print(f"❌ 画像保存エラー: {e}")
        return None

async def save_drawing(user_id: str, image_path: str = None, analysis_a: str = None, analysis_b: str = None, notes: str = None):
    try:
        data = {"user_id": user_id, "image_path": image_path, "analysis_mode_a": analysis_a, "analysis_mode_b": analysis_b, "notes": notes}
        supabase.table("drawings").insert(data).execute()
    except Exception as e:
        print(f"❌ Drawing保存エラー: {e}")

async def update_analysis_b(user_id: str, analysis_b: str):
    try:
        result = supabase.table("drawings").select("id").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if result.data:
            record_id = result.data[0]["id"]
            tags = extract_tags(analysis_b)
            update_data = {"analysis_mode_b": analysis_b}
            if tags:
                update_data["tags"] = tags
            supabase.table("drawings").update(update_data).eq("id", record_id).execute()
            if tags:
                await ingest_all_concepts(user_id, tags, analysis_b, "", record_id)
            return True
        return False
    except Exception as e:
        print(f"❌ モードB更新エラー: {e}")
        return False


async def update_analysis_b_with_notes(user_id: str, analysis_b: str):
    try:
        result = supabase.table("drawings").select("id, notes").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if result.data:
            record = result.data[0]
            record_id = record["id"]
            notes = record.get("notes") or ""
            tags = extract_tags(analysis_b)
            update_data = {"analysis_mode_b_with_notes": analysis_b}
            if tags:
                update_data["tags"] = tags
            supabase.table("drawings").update(update_data).eq("id", record_id).execute()
            if tags:
                await ingest_all_concepts(user_id, tags, analysis_b, notes, record_id)
            return True
        return False
    except Exception as e:
        print(f"❌ ちなみにモードB更新エラー: {e}")
        return False


async def update_notes(user_id: str, notes: str):
    try:
        result = supabase.table("drawings").select("id").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if result.data:
            record_id = result.data[0]["id"]
            supabase.table("drawings").update({"notes": notes}).eq("id", record_id).execute()
            return True
        return False
    except Exception as e:
        print(f"❌ メモ更新エラー: {e}")
        return False

async def update_analysis_b_direct(user_id: str, analysis_b: str, background_tasks: BackgroundTasks):
    try:
        result = supabase.table("drawings").select("id").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if result.data:
            record_id = result.data[0]["id"]
            supabase.table("drawings").update({"analysis_mode_b": analysis_b}).eq("id", record_id).execute()
            return True
        return False
    except Exception as e:
        print(f"❌ モードB直接更新エラー: {e}")
        return False

@app.get("/")
@app.head("/")

async def health_check():
    return {"status": "ok"}

@app.post("/callback")

async def callback(request: Request, background_tasks: BackgroundTasks):
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
                            
                    if user_message.startswith("ちなみに"):
                        if user_id in last_image_store:
                            notes = user_message.replace("ちなみに", "").strip()
                            await update_notes(user_id, notes)
                            await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                            image_data = last_image_store[user_id]
                            background_tasks.add_task(handle_detail_command, user_id, image_data, notes)
                        else:
                            await reply_message(reply_token, "先に絵の写真を送ってください📷")
                            
                    elif user_message == "詳しく":
                        if user_id in last_image_store:
                            await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                            image_data = last_image_store[user_id]
                            background_tasks.add_task(handle_detail_command, user_id, image_data, None)
                        else:
                            await reply_message(reply_token, "先に絵の写真を送ってください📷")
                    
                    elif user_message == "振り返って":
                        await reply_message(reply_token, "🎨 これまでの絵を振り返っています…少しだけお待ちください")
                        try:
                            past_records = supabase.table("drawings")\
                                .select("id, created_at, analysis_mode_b, analysis_mode_b_with_notes")\
                                .eq("user_id", user_id)\
                                .order("created_at", desc=True)\
                                .limit(5)\
                                .execute()
                            if not past_records.data:
                                await push_message(user_id, "まだ絵の記録が十分にありません。絵をもう少し送ってみてください📷")
                            else:
                                drawing_ids = [r["id"] for r in past_records.data]
                                analyses = []
                                for i, record in enumerate(reversed(past_records.data), 1):
                                    analysis = record.get("analysis_mode_b_with_notes") or record.get("analysis_mode_b")
                                    if analysis:
                                        date = record["created_at"][:10]
                                        analyses.append(f"【{i}枚目 {date}】\n{analysis}")
                                past_analyses = "\n\n".join(analyses) if analyses else ""
                                current_analysis = analyses[-1] if analyses else ""
                                review_result = await analyze_review(current_analysis, past_analyses)
                                if not review_result.startswith("⚠️"):
                                    await save_review(user_id, review_result, drawing_ids)
                                await push_message(user_id, review_result)
                        except Exception as e:
                            print(f"❌ 振り返りエラー: {e}")
                            await push_message(user_id, "⚠️ 振り返りに失敗しました。もう一度お試しください。")
                    else:
                        await reply_message(reply_token, "絵の写真を送ってください📷")
                        
                elif message["type"] == "image":
                    image_id = message["id"]
                    await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                    image_data = await get_line_image(image_id)
                    if image_data:
                        # 全概念ページのsummaryを取得してAモードに渡す
                        wiki_context = await get_all_wiki_summaries(user_id)
                        analysis_result = await analyze_with_dify(image_data, mode="quick", wiki_context=wiki_context)
                        if analysis_result.startswith("⚠️"):
                            await push_message(user_id, analysis_result)
                        else:
                            last_image_store[user_id] = image_data
                            image_path = await save_image(user_id, image_data)
                            await save_drawing(user_id, image_path=image_path, analysis_a=analysis_result)
                            await push_message(user_id, analysis_result)
                            await push_message(user_id, "💡「詳しく」→ より詳細な分析\n💡「ちなみに〇〇」→ 付帯情報を加えた詳細分析\n💡「振り返って」→ これまでの絵を振り返る")
                    else:
                        await push_message(user_id, "⚠️ 画像の取得に失敗しました。もう一度お試しください。")
                        
    except Exception as e:
        print(f"❌ Webhookエラー: {e}")
    return {"status": "ok"}


