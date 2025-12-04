from google import genai
from google.genai import types
from PIL import Image
import os
from pathlib import Path
import base64
from dotenv import load_dotenv
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
import io


load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def generate_card_news(summary, output_path):
    chat = client.chats.create(
        model="gemini-3-pro-image-preview",
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        )
    )
    message = f"""
Create a professional business-style card-news image (1080x1080) for executive-level reporting.
Use a clean and formal layout similar to global oil market briefings, with the style shown in high-level economic dashboards.
=== STYLE REQUIREMENTS (STRICT) ===
- Absolutely NO cute, cartoon, or playful style
- Use a formal, executive-level design similar to Deloitte / IEA / Bloomberg reports
- Color palette: navy, deep blue, gray, white, black (no bright or neon colors)
- Use flat icons only (simple, monochrome professional icons)
- Use clear box layout with 2–3 segmented sections
- Include chart-like elements (line chart or bar chart) that visually summarize the trend
- Typography hierarchy:
  1) Large headline (top)
  2) Two or three sub-sections with labels
  3) Key numerical indicators
  4) Bullet-point insights at the bottom
- Use subtle shadows or thin borders for separation
- Ensure high readability and clean spacing
=== CONTENT TO VISUALIZE ===
The card must organize the following summary into a structured business infographic:
{summary}
=== STRUCTURE (VERY IMPORTANT) ===
Top Section:
- Main headline derived from the summary (short, strong, business tone)
Middle Section (split into 2 or 3 columns):
- Column 1: A simplified line or bar chart illustrating the trend described in the summary
- Column 2: Key quantitative indicators (prices, volumes, growth %, supply/demand values)
- Column 3 (optional): Impact factors or drivers (icons + short labels)
Bottom Section:
- 3–4 short bullet insights summarizing the implications
- Use concise business language suitable for executives
=== TEXT RULES ===
- All text must be in English.
- Use short, high-impact phrases.
- Avoid long sentences.
- Do NOT invent unrelated data — infer only from the summary.
- If the summary lacks numeric data, create generic but realistic placeholders (e.g., “Price Downtrend”, “Demand Weakening”).
Generate ONLY one image part as the result.
"""
    print(summary)
    response = chat.send_message(message)
    print(response)

    # 디렉토리 생성
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 1) response.candidates 구조 확인
    candidates = response.candidates
    if not candidates:
        print("응답에 candidates 없음")
        return None

    parts = candidates[0].content.parts
    if not parts:
        print("응답에 content.parts 없음")
        return None

    # 2) parts에서 inline_data 찾기
    for part in parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            img_bytes = part.inline_data.data
            mime_type = part.inline_data.mime_type  # image/jpeg 등

            # byte → 파일 저장 (webp로 강제변환)
            try:
                img = Image.open(io.BytesIO(img_bytes))
                img.save(output_path, format="WEBP")
                print("WebP 이미지 저장:", output_path)
                return output_path
            except Exception as e:
                print("이미지 저장 실패:", e)
                continue

    print("IMAGE part를 찾지 못했습니다.")
    return None

def generate_top5_cards(articles, output_dir="app/ai/repository/data/images"):
    """
    입력:
        - articles: 뉴스 리스트
        - output_dir: 이미지가 저장될 폴더 경로 (폴더만)
    출력:
        {
            "top5_articles": [...뉴스 5개...],
            "card_images": [...이미지 path 리스트...]
        }
    """
    # 1) sentiment.score 기준 Top-5 정렬
    sorted_articles = sorted(
        articles,
        key=lambda x: x.get("sentiment", {}).get("score", 0),
        reverse=True
    )
    top5 = sorted_articles[:5]
    # 2) 저장 폴더 생성
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []
    # 3) 카드뉴스 생성
    for idx, article in enumerate(top5):
        summary = article.get("summary")
        if not summary:
            continue
        img_path = output_dir / f"top5_card_{idx}.webp"
        result = generate_card_news(summary, str(img_path))
        if result:
            image_paths.append(result)
            print(f"[Top5-{idx}] 카드뉴스 생성 완료 → {result}")
    return {
        "top5_articles": top5,
        "card_images": image_paths
    }