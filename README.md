# Amulet 🎨

> 娘の絵をAIが分析し、親の観察眼を拡張するLINE Bot


![Python](https://img.shields.io/badge/Python-3.11-blue)
(https://python.org)


![FastAPI](https://img.shields.io/badge/FastAPI-latest-green)
(https://fastapi.tiangolo.com)

![Dify](https://img.shields.io/badge/Dify-latest-orange)
(https://dify.ai)

## 概要

LINEで子供の絵の写真を送ると、レッジョ・エミリア教育・脳科学・発達心理学などの知見を持つAIが分析し、親の観察眼を拡張します。

**親がより豊かに子供と向き合うためのツールです。**

## 機能

### モードA「その場で楽しむ」
娘が絵を持ってきたその瞬間に使います。この絵にしか使えない具体的な問いかけを3つ提案します。

### モードB「深く知る」
「詳しく」と送ると起動します。4つのセクションで構成された深い分析を返します。

- 【描いている間に起きていること】
- 【この子が見ている世界】
- 【より一緒に楽しむ】
- 【残しておきたい成長】

### 付帯情報の追加
「ちなみに〇〇」と送ると、付帯情報を加えたモードBの分析が返ります。　

例：「ちなみに真ん中の塔は東京タワーだって」

## システム構成
```
スマホ（LINE）
　↓ 娘の絵の写真を送る
LINEサーバー（Messaging API）
　↓ Webhookで転送
Render.com（Python/FastAPI）
　↓ 画像を取得してDifyへ転送
Dify（ワークフロー）
　↓ Geminiで画像分析
Render.com
　↓ 結果をLINEへ返送
スマホ（LINE）
　↓ 分析結果が届く
```

## 技術スタック

| サービス | 役割 |
|---------|------|
| LINE Messaging API | フロントエンド |
| Python / FastAPI | 中間サーバー |
| Render.com | ホスティング |
| Dify | AIワークフロー |
| Gemini 2.5 Flash Lite | LLM（画像分析） |
| Supabase | データベース・画像ストレージ |

## セットアップ

### 必要な環境変数

Render.comの環境変数に以下を設定してください。

| Key | 説明 |
|-----|------|
| LINE_CHANNEL_ACCESS_TOKEN | LINEのチャンネルアクセストークン |
| LINE_CHANNEL_SECRET | LINEのチャンネルシークレット |
| DIFY_API_KEY | DifyモードAのAPIキー |
| DIFY_API_KEY_DETAIL | DifyモードBのAPIキー |
| DIFY_API_URL | DifyのAPIサーバーURL |
| SUPABASE_URL | SupabaseのプロジェクトURL |
| SUPABASE_KEY | Supabaseのanon publicキー |

### Supabaseのテーブル設計

```sql
create extension if not exists vector;

create table drawings (
  id uuid default gen_random_uuid() primary key,
  user_id text not null,
  created_at timestamp with time zone default now(),
  image_date date default current_date,
  image_path text,
  analysis_mode_a text,
  analysis_mode_b text,
  notes text,
  tags text[],
  embedding vector(768)
);
```

## 使い方
```
LINEでボットを友だち追加する
子供の絵の写真を送る → モードAの分析が返ってくる
「詳しく」と送る → モードBの詳細分析が返ってくる
「ちなみに〇〇」と送る → 付帯情報を加えた詳細分析が返ってくる
```

## 設計思想
```
Negative Constraints
このシステムには「絶対にしないこと」を明示的に定義しています。
比較しない（他の子供・発達の標準値との比較禁止）
評価しない（「上手い」「すごい」などの評価語禁止）
診断しない（発達の遅れを示唆する表現禁止）
断定・予言しない（才能や将来の決めつけ禁止）
親に処方しない（「〇〇すべき」という指導禁止）
```

## 今後の予定
```
Layer 1：分析結果の蓄積（✅ 実装済み）
Layer 2：LLM Wiki型の長期記憶（概念ページの自動更新）
Layer 3：GraphRAGによる関係性の構造化
```

## 関連記事
```
ビジネスサイド出身がはじめてAIソリューションを作ってみた話
AIが娘の成長を「理解する」ために：Amuletの長期記憶設計
Author
ai-zawa
Zenn: ai_zawa
GitHub: ai-zawa
```
