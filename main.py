from fastapi import FastAPI, Request, BackgroundTasks
import httpx
import os
import uuid
import json
import asyncio
from datetime import datetime, timezone
from supabase import create_client
import hmac
import hashlib
import base64

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY")
DIFY_API_KEY_DETAIL = os.environ.get("DIFY_API_KEY_DETAIL")
DIFY_API_URL = os.environ.get("DIFY_API_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DIFY_API_KEY_REVIEW = os.environ.get("DIFY_API_KEY_REVIEW")
DIFY_API_KEY_NORMALIZE = os.environ.get("DIFY_API_KEY_NORMALIZE")
DIFY_API_KEY_UPDATE = os.environ.get("DIFY_API_KEY_UPDATE")
INGEST_MAX_RETRIES = int(os.environ.get("INGEST_MAX_RETRIES", "0"))
INGEST_MAX_WAIT = int(os.environ.get("INGEST_MAX_WAIT", "30"))
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

# Storage読み書き用の管理者クライアント
supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_SERVICE_KEY else supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

last_image_store = {}

ALLOWED_USER_IDS = set(
    uid.strip()
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
)


def is_allowed_user(user_id: str) -> bool:
    """招待済みユーザーかを判定する（リスト未設定なら全員拒否）"""
    return user_id in ALLOWED_USER_IDS


def verify_line_signature(body: bytes, signature: str) -> bool:
    """LINEからの正規リクエストであることを署名で検証する"""
    if not signature or not LINE_CHANNEL_SECRET:
        return False
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)



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


async def analyze_with_dify(image_data: bytes, mode: str = "quick", notes: str = None, wiki_context: str = None, max_retries: int = None):
    if max_retries is None:
        max_retries = INGEST_MAX_RETRIES
    api_key = DIFY_API_KEY_DETAIL if mode == "detail" else DIFY_API_KEY
    upload_url = f"{DIFY_API_URL}/files/upload"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 画像アップロード（1回だけ）
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

            # ワークフロー実行（503等はリトライ）
            for attempt in range(max_retries + 1):
                run_response = await client.post(run_url, headers=headers, json=payload)

                if run_response.status_code == 200:
                    result = run_response.json()
                    text = result.get("data", {}).get("outputs", {}).get("text")
                    if text:
                        return text
                    # 200だが空 → リトライ
                    if attempt < max_retries:
                        wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                        print(f"分析結果が空。{wait}秒待機してリトライ（{attempt + 1}回目）")
                        await asyncio.sleep(wait)
                        continue
                    return "⚠️ 分析結果を取得できませんでした。もう一度お試しください。"

                if run_response.status_code in (503, 504, 429):
                    print(f"分析リトライ対象エラー(status={run_response.status_code})。{attempt + 1}回目")
                    if attempt < max_retries:
                        wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                        print(f"{wait}秒待機してリトライします")
                        await asyncio.sleep(wait)
                        continue
                    return "⚠️ 現在アクセスが集中しています。しばらく時間をおいてお試しください。"

                print(f"Dify分析エラー: status={run_response.status_code}")
                return "⚠️ 分析に失敗しました。もう一度お試しください。"

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


# ===== 変更: get_wiki_page（category対応） =====
async def get_wiki_page(user_id: str, concept: str, category: str) -> dict:
    try:
        result = supabase.table("wiki_pages")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("concept", concept)\
            .eq("category", category)\
            .execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"❌ Wiki取得エラー: {e}")
        return None


# ===== 変更: get_existing_concepts（category絞り込み） =====
async def get_existing_concepts(user_id: str, category: str = None) -> list:
    try:
        query = supabase.table("wiki_pages").select("concept").eq("user_id", user_id)
        if category:
            query = query.eq("category", category)
        result = query.execute()
        return [r["concept"] for r in result.data] if result.data else []
    except Exception as e:
        print(f"❌ 概念一覧取得エラー: {e}")
        return []


# ===== 変更: get_wiki_context（複合キー対応・カテゴリ付きで文脈化） =====
async def get_wiki_context(user_id: str, tags: list) -> str:
    try:
        if not tags:
            return ""

        result = supabase.table("wiki_pages")\
            .select("concept, category, summary")\
            .eq("user_id", user_id)\
            .in_("concept", tags)\
            .execute()

        if not result.data:
            return ""

        context_parts = []
        for page in result.data:
            concept = page.get("concept")
            category = page.get("category", "")
            summary = page.get("summary")
            if concept and summary:
                context_parts.append(f"【{concept}（{category}）】\n{summary}")

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


# ===== 変更: save_wiki_page（category保存） =====
async def save_wiki_page(user_id: str, wiki_data: dict, drawing_id: str, concept: str, original_concept: str, category: str):
    try:
        if not concept:
            print(f"conceptが空のためスキップ")
            return

        # 既存ページは (user_id, concept, category) で取得
        existing = await get_wiki_page(user_id, concept, category)

        # タイムラインの結合（過去を保持して新規を追加）
        timeline = []
        if existing and existing.get("timeline"):
            timeline = existing.get("timeline")
            if isinstance(timeline, str):
                timeline = json.loads(timeline)
        new_entry = wiki_data.get("new_timeline_entry")
        if new_entry:
            # 将来の分化・階層化に備えたメタデータ（遺伝子）を埋め込む
            new_entry["_metadata"] = {
                "original_concept": original_concept,  # 名寄せ前の生タグ
                "drawing_id": drawing_id               # 一次情報へのポインタ
            }
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
            "category": category,
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
            supabase.table("wiki_pages").update(data)\
                .eq("user_id", user_id).eq("concept", concept).eq("category", category).execute()
        else:
            supabase.table("wiki_pages").insert(data).execute()

        print(f"Wiki保存（差分マージ）完了: concept={concept}[{category}]（生タグ: {original_concept}）")
    except Exception as e:
        print(f"❌ Wiki保存エラー: {e}")


# ===== 変更: enqueue_failed_update（category対応） =====
async def enqueue_failed_update(user_id: str, drawing_id: str, original_concept: str, normalized_concept: str, category: str, analysis: str, notes: str, needs_normalize: bool = False):
    """処理に失敗したタグを再処理キューに記録する"""
    try:
        data = {
            "user_id": user_id,
            "drawing_id": drawing_id,
            "original_concept": original_concept,
            "normalized_concept": normalized_concept,
            "category": category,
            "analysis": analysis,
            "notes": notes or "",
            "needs_normalize": needs_normalize,  # 名寄せが必要かを明示
            "status": "pending"
        }
        supabase.table("ingest_queue").insert(data).execute()
        flag = "（要名寄せ）" if needs_normalize else ""
        print(f"📥 キューに記録{flag}: {normalized_concept}[{category}]（生タグ: {original_concept}）")
    except Exception as e:
        print(f"❌ キュー記録エラー: {e}")


# ===== 変更: run_normalize（category受け取り・同カテゴリ照合） =====
async def run_normalize(user_id: str, concept: str, category: str, analysis: str, max_retries: int = None):
    if max_retries is None:
        max_retries = INGEST_MAX_RETRIES

    # 同じカテゴリの既存概念だけを取得（カテゴリ跨ぎ名寄せを禁止）
    existing_concepts = await get_existing_concepts(user_id, category)
    existing_concepts_str = ", ".join(existing_concepts) if existing_concepts else ""
    print(f"名寄せ開始: concept={concept}[{category}], 既存({category}): {existing_concepts_str}")

    today = datetime.now(timezone.utc).date().isoformat()
    analysis_with_date = f"[{today}]\n{analysis}"

    headers = {"Authorization": f"Bearer {DIFY_API_KEY_NORMALIZE}"}
    run_url = f"{DIFY_API_URL}/workflows/run"

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "inputs": {
                        "concept": concept,
                        "analysis": analysis_with_date,
                        "existing_concepts": existing_concepts_str
                    },
                    "response_mode": "blocking",
                    "user": "line-user"
                }
                response = await client.post(run_url, headers=headers, json=payload)

                if response.status_code == 200:
                    result = response.json()
                    outputs = result.get("data", {}).get("outputs", {})
                    tags = outputs.get("tags", [])
                    if tags:
                        return tags
                    else:
                        print(f"名寄せ結果が空: outputs={outputs}")
                        if attempt < max_retries:
                            wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                            await asyncio.sleep(wait)
                            continue
                        return []

                if response.status_code in (503, 504, 429):
                    print(f"名寄せリトライ(status={response.status_code})。{attempt + 1}回目")
                    if attempt < max_retries:
                        wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                        await asyncio.sleep(wait)
                        continue

                print(f"名寄せエラー: status={response.status_code}")
                return []
        except Exception as e:
            print(f"❌ 名寄せエラー: {e}")
            if attempt < max_retries:
                wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                await asyncio.sleep(wait)
                continue
            return []
    return []


# ===== 変更: run_update（category対応） =====
async def run_update(user_id: str, normalized_concept: str, original_concept: str, category: str, analysis: str, notes: str, drawing_id: str, max_retries: int = None):
    if max_retries is None:
        max_retries = INGEST_MAX_RETRIES

    has_notes = "true" if notes else "false"
    today = datetime.now(timezone.utc).date().isoformat()
    analysis_with_date = f"[{today}]\n{analysis}"

    headers = {"Authorization": f"Bearer {DIFY_API_KEY_UPDATE}"}
    run_url = f"{DIFY_API_URL}/workflows/run"

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                payload = {
                    "inputs": {
                        "concept": normalized_concept,
                        "analysis": analysis_with_date,
                        "notes": notes or "",
                        "has_notes": has_notes,
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

                    # 終了ノードの出力を取り出す（キー名の揺れに両対応）
                    bundled_str = ""
                    if "bundled" in outputs:
                        bundled_str = outputs["bundled"]
                    elif "output" in outputs:
                        val = outputs["output"]
                        bundled_str = val[0] if isinstance(val, list) and val else val
                    if isinstance(bundled_str, list):
                        bundled_str = bundled_str[0] if bundled_str else ""

                    if bundled_str:
                        bundled = json.loads(bundled_str)
                        norm_concept = bundled["concept"]
                        wiki_data = json.loads(bundled["diff"])
                        await save_wiki_page(user_id, wiki_data, drawing_id, norm_concept, original_concept, category)
                        return True
                    else:
                        print(f"update出力が空: outputs={outputs}")
                        if attempt < max_retries:
                            wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                            print(f"updateリトライ（{attempt + 1}回目、{wait}秒）")
                            await asyncio.sleep(wait)
                            continue
                        return False

                if response.status_code in (503, 504, 429):
                    print(f"updateリトライ(status={response.status_code})。{attempt + 1}回目")
                    if attempt < max_retries:
                        wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                        await asyncio.sleep(wait)
                        continue
                print(f"updateエラー: status={response.status_code}")
                return False
        except Exception as e:
            print(f"❌ updateエラー: {e}")
            if attempt < max_retries:
                wait = min(2 ** (attempt + 1), INGEST_MAX_WAIT)
                await asyncio.sleep(wait)
                continue
            return False
    return False

# ===== 変更: ingest_all_concepts（(タグ,カテゴリ)対応） =====
async def ingest_all_concepts(user_id: str, tagged_tags: list, analysis: str, notes: str, record_id: str):
    # tagged_tags = [(タグ名, カテゴリ), ...]

    # 1. 各タグを、同カテゴリ内で名寄せ
    concept_triples = []  # (original, normalized, category)
    for original_concept, category in tagged_tags:
        normalized_list = await run_normalize(user_id, original_concept, category, analysis)
        if normalized_list:
            for norm in normalized_list:
                concept_triples.append((original_concept, norm, category))
        else:
            print(f"⚠️ 名寄せ失敗: {original_concept}[{category}]")
            # 名寄せ失敗 → needs_normalize=True でキューに積む
            await enqueue_failed_update(user_id, record_id, original_concept, original_concept, category, analysis, notes, needs_normalize=True)
        await asyncio.sleep(1)

    # 2. 概念ページ更新
    failed = []
    for original_concept, normalized_concept, category in concept_triples:
        success = await run_update(user_id, normalized_concept, original_concept, category, analysis, notes, record_id)
        if not success:
            print(f"⚠️ update失敗: {normalized_concept}[{category}]（生タグ: {original_concept}）")
            failed.append((original_concept, normalized_concept, category))
        await asyncio.sleep(2)

    # 3. 更新失敗分を30秒後に再挑戦
    if failed:
        print(f"🔁 update再挑戦: {len(failed)}件")
        await asyncio.sleep(30)
        for original_concept, normalized_concept, category in failed:
            success = await run_update(user_id, normalized_concept, original_concept, category, analysis, notes, record_id)
            if not success:
                print(f"❌ 再挑戦も失敗 → キューに記録: {normalized_concept}[{category}]")
                # 更新失敗 → 名寄せは済んでいるので needs_normalize=False
                await enqueue_failed_update(user_id, record_id, original_concept, normalized_concept, category, analysis, notes, needs_normalize=False)
            await asyncio.sleep(3)


# ===== 変更: process_ingest_queue（category対応） =====
async def process_ingest_queue(user_id: str = None, limit: int = 20):
    """pendingのキューを再処理する"""
    try:
        query = supabase.table("ingest_queue").select("*").eq("status", "pending")
        if user_id:
            query = query.eq("user_id", user_id)
        result = query.limit(limit).execute()

        if not result.data:
            print("キューは空です")
            return 0

        processed = 0
        for row in result.data:
            queue_id = row["id"]
            uid = row["user_id"]
            original_concept = row["original_concept"]
            normalized_concept = row["normalized_concept"]
            category = row.get("category")
            analysis = row["analysis"]
            notes = row["notes"]
            drawing_id = row["drawing_id"]

            # このループ内で名寄せをやり直して成功したか
            normalize_succeeded = False

            # 名寄せが必要なキューは、まず同カテゴリ内で名寄せをやり直す
            if row.get("needs_normalize"):
                normalized_list = await run_normalize(uid, original_concept, category, analysis)
                if not normalized_list:
                    new_count = (row.get("retry_count") or 0) + 1
                    new_status = "failed" if new_count >= 5 else "pending"
                    supabase.table("ingest_queue").update({
                        "retry_count": new_count,
                        "status": new_status,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", queue_id).execute()
                    print(f"⚠️ 再名寄せ失敗（{new_count}回目）: {original_concept}[{category}]")
                    await asyncio.sleep(2)
                    continue
                normalized_concept = normalized_list[0]
                normalize_succeeded = True

            # 概念ページ更新
            success = await run_update(uid, normalized_concept, original_concept, category, analysis, notes, drawing_id)
            if success:
                supabase.table("ingest_queue").update({
                    "status": "done",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", queue_id).execute()
                processed += 1
                print(f"✅ キュー処理成功: {normalized_concept}[{category}]")
            else:
                new_count = (row.get("retry_count") or 0) + 1
                new_status = "failed" if new_count >= 5 else "pending"

                update_data = {
                    "retry_count": new_count,
                    "status": new_status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                # 今回名寄せに成功していたなら、その成果を保存してフラグを折る
                if normalize_succeeded:
                    update_data["normalized_concept"] = normalized_concept
                    update_data["needs_normalize"] = False

                supabase.table("ingest_queue").update(update_data).eq("id", queue_id).execute()
                print(f"⚠️ キュー処理失敗（{new_count}回目、status={new_status}）: {normalized_concept}[{category}]")
            await asyncio.sleep(2)

        print(f"キュー処理完了: {processed}/{len(result.data)} 件成功")
        return processed
    except Exception as e:
        print(f"❌ キュー処理エラー: {e}")
        return 0


# ===== 変更: extract_tags（(タグ名, カテゴリ) のペアで抽出） =====
def extract_tags(analysis_text: str) -> list:
    """モードB出力から (タグ名, カテゴリ) のペアを抽出する"""
    import re

    category_labels = {
        "モチーフ": "モチーフ",
        "素材": "素材",
        "行為感覚": "行為感覚",
        "コンテキスト": "コンテキスト",
    }

    tagged = []
    try:
        for label, category in category_labels.items():
            # 行頭の記号（・ ･ - * 等）は任意。「モチーフ：タグ名」に対応
            pattern = rf"(?:^|\n)\s*[・･\-\*]?\s*{label}\s*[:：]\s*(.+)"
            match = re.search(pattern, analysis_text)
            if match:
                tag = match.group(1).strip()
                # 角括弧などが残っていたら除去（保険）
                tag = tag.strip("[]［］「」 ").strip()
                if tag and tag != "なし" and len(tag) <= 15:
                    tagged.append((tag, category))
    except Exception as e:
        print(f"❌ タグ抽出エラー: {e}")

    return tagged


# 「詳しく」「ちなみに」の重い処理をまとめてバックグラウンドで実行する関数
    async def handle_detail_command(user_id: str, image_data: bytes, notes: str = None, record_id: str = None):
    try:
        # ① モードB(1回目・wiki_contextなし) ← Ingest用の一次情報
        analysis_for_ingest = await analyze_with_dify(image_data, mode="detail", notes=notes)
        if analysis_for_ingest.startswith("⚠️"):
            await push_message(user_id, analysis_for_ingest)
            return

        # ② タグ抽出（(タグ名, カテゴリ) のペアのリスト）
        tagged_tags = extract_tags(analysis_for_ingest)

        # ③ wiki_context取得（タグ名だけ取り出して渡す・軽い・名寄せなし）
        tag_names = [t[0] for t in tagged_tags]
        wiki_context = await get_wiki_context(user_id, tag_names)

        # ④ モードB(2回目・wiki_contextあり) ← 親に見せる出力
        if wiki_context:
            analysis_for_parent = await analyze_with_dify(image_data, mode="detail", notes=notes, wiki_context=wiki_context)
        else:
            analysis_for_parent = analysis_for_ingest

        # ⑤ drawings保存（tagsはタグ名だけ・案A / Ingestはしない・record_idを得る）
        record_id = await save_analysis_only(user_id, record_id, analysis_for_ingest, tag_names, notes)

        # ⑥ 先にユーザーへpush（Wiki更新を待たせない）
        await push_message(user_id, analysis_for_parent)

        # ⑦ ユーザー応答の後で、Wiki更新（tagged_tagsを渡す・重い・503でも粘る）
        if tagged_tags and record_id:
            await ingest_all_concepts(user_id, tagged_tags, analysis_for_ingest, notes or "", record_id)

    except Exception as e:
        print(f"❌ handle_detail_commandエラー: {e}")
        await push_message(user_id, "⚠️ 処理中にエラーが発生しました。もう一度お試しください。")


async def save_image(user_id: str, image_data: bytes) -> str:
    try:
        file_name = f"{user_id}/{uuid.uuid4()}.jpg"
        supabase_admin.storage.from_("drawings").upload(file_name, image_data, {"content-type": "image/jpeg"})
        return file_name
    except Exception as e:
        print(f"❌ 画像保存エラー: {e}")
        return None


async def save_drawing(user_id: str, image_path: str = None, analysis_a: str = None, analysis_b: str = None, notes: str = None) -> str:
    try:
        data = {"user_id": user_id, "image_path": image_path, "analysis_mode_a": analysis_a, "analysis_mode_b": analysis_b, "notes": notes}
        result = supabase.table("drawings").insert(data).execute()
        if result.data:
            return result.data[0]["id"]
        return None
    except Exception as e:
        print(f"❌ Drawing保存エラー: {e}")
        return None


async def restore_last_image(user_id: str):
    """メモリに画像がないとき、直近のdrawingsレコードとStorageから復元する"""
    try:
        result = supabase.table("drawings")\
            .select("id, image_path")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        if not result.data:
            return None
        record = result.data[0]
        image_path = record.get("image_path")
        if not image_path:
            return None
        image_data = supabase_admin.storage.from_("drawings").download(image_path)
        if image_data:
            return {"image_data": image_data, "record_id": record["id"]}
        return None
    except Exception as e:
        print(f"❌ 画像復元エラー: {e}")
        return None


async def save_analysis_only(user_id: str, record_id: str, analysis_b: str, tags: list, notes: str = None) -> str:
    """指定されたdrawingsレコードにanalysisとtagsを保存する（Ingestはしない）"""
    try:
        if not record_id:
            return None

        update_data = {}
        
        if notes:
            update_data["analysis_mode_b_with_notes"] = analysis_b
        else:
            update_data["analysis_mode_b"] = analysis_b
        if tags:
            update_data["tags"] = tags

        supabase.table("drawings").update(update_data).eq("id", record_id).execute()
        return record_id
    except Exception as e:
        print(f"❌ analysis保存エラー: {e}")
        return None


async def update_notes(record_id: str, notes: str):
    try:
        if not record_id:
            return False
        supabase.table("drawings").update({"notes": notes}).eq("id", record_id).execute()
        return True
    except Exception as e:
        print(f"❌ メモ更新エラー: {e}")
        return False

@app.get("/")
@app.head("/")
async def health_check():
    # Supabaseの自動pause防止（無料プランは1週間無アクセスで停止するため）
    try:
        supabase.table("wiki_pages").select("id").limit(1).execute()
    except Exception as e:
        print(f"⚠️ health check DB error: {e}")
    return {"status": "ok"}

@app.post("/callback")

async def callback(request: Request, background_tasks: BackgroundTasks):
    # A-1: 署名検証（LINE以外からのリクエストを遮断）
    body_bytes = await request.body()
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_line_signature(body_bytes, signature):
        print("⚠️ 署名検証に失敗（LINE以外からのリクエストの可能性）")
        return {"status": "invalid signature"}

    try:
        body = json.loads(body_bytes)
        events = body.get("events", [])
        for event in events:
            if event.get("type") == "message":
                reply_token = event["replyToken"]
                message = event["message"]
                user_id = event["source"]["userId"]

                # A-2: 招待制（未登録ユーザーは丁重にお断り）
                if not is_allowed_user(user_id):
                    print(f"⚠️ 未招待ユーザーからのアクセス: {user_id}")
                    await reply_message(reply_token, "Amuletは現在、招待制で運用しています🙏")
                    continue

                if message["type"] == "text":
                    user_message = message["text"].strip()

                    if user_message.startswith("ちなみに"):
                        entry = last_image_store.get(user_id)
                        if not entry:
                            entry = await restore_last_image(user_id)
                            if entry:
                                last_image_store[user_id] = entry
                        if entry:
                            notes = user_message.replace("ちなみに", "").strip()
                            await update_notes(entry["record_id"], notes)
                            await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                            background_tasks.add_task(handle_detail_command, user_id, entry["image_data"], notes, entry["record_id"])
                        else:
                            await reply_message(reply_token, "先に絵の写真を送ってください📷")

                    elif user_message == "詳しく":
                        entry = last_image_store.get(user_id)
                        if not entry:
                            entry = await restore_last_image(user_id)
                            if entry:
                                last_image_store[user_id] = entry
                        if entry:
                            await reply_message(reply_token, "🎨 絵を見ています…少しだけお待ちください")
                            background_tasks.add_task(handle_detail_command, user_id, entry["image_data"], None, entry["record_id"])
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

                    elif user_message == "キュー処理":
                        await reply_message(reply_token, "🔄 未処理のWiki更新を再開します…")
                        processed = await process_ingest_queue(user_id)
                        await push_message(user_id, f"✅ {processed}件のWiki更新を処理しました")

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
                            image_path = await save_image(user_id, image_data)
                            record_id = await save_drawing(user_id, image_path=image_path, analysis_a=analysis_result)
                            last_image_store[user_id] = {"image_data": image_data, "record_id": record_id}
                            await push_message(user_id, analysis_result)
                            await push_message(user_id, "💡「詳しく」→ より詳細な分析\n💡「ちなみに〇〇」→ 付帯情報を加えた詳細分析\n💡「振り返って」→ これまでの絵を振り返る")
                    else:
                        await push_message(user_id, "⚠️ 画像の取得に失敗しました。もう一度お試しください。")

    except Exception as e:
        print(f"❌ Webhookエラー: {e}")
    return {"status": "ok"}
