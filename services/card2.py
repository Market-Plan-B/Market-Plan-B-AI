from google import genai
from google.genai import types
from PIL import Image
import os
from pathlib import Path
import base64
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def generate_card_news(summary, output_path=r"D:\skax\project_skala\app\repository\data\images"):

    prompt = f"""
Create a single square card-news style image (1080x1080).
Use a clean, minimalist layout with simple geometric shapes and flat icons.
Avoid realism; flat design only.
Top caption: "Global Oil Market Snapshot"
Main title: Generate a short English title (3–5 words)
Subtitle: Short English explanation
Two bullet key-points: Extract two insights
Illustration:
- simple flat illustration of oil barrel or earth
- cute or character-style icon allowed
Color palette: 2–3 flat colors.
All text must be in English.
SUMMARY: "{summary}"
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",  # :경고: 이미지 지원 모델 사용
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"]
        )
    )
    for part in response.parts:
        if part.inline_data:
            img = part.as_image()
            img.save(output_path)
            print(":두꺼운_확인_표시: 이미지 저장:", output_path)
            return
        
    print(":x: 이미지 생성 실패 (inline_data 없음)")