import json
import numpy as np
import uuid
from dotenv import load_dotenv
from tqdm import tqdm
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.decomposition import TruncatedSVD
from transformers import AutoTokenizer, AutoModel
import torch
import os
from openai import OpenAI
import pymysql
from app.services.card2 import generate_card_news  

load_dotenv()

# CrudeBERT 모델 로드
def load_crudebert():
    tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
    model = AutoModel.from_pretrained("Captain-1337/CrudeBERT")
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    return tokenizer, model, device

# 임베딩 함수
def crudebert_embedding(text, tokenizer, model, device):
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        padding=True,
        max_length=512
    ).to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    # CLS 토큰 임베딩
    emb = outputs.last_hidden_state[:, 0, :].cpu().numpy()[0]
    return emb

# 데이터 로드
def load_from_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
def load_from_db(conn):
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM oil_news")
    return cursor.fetchall()

# 중복 제거(제목)
def remove_duplicate_titles(news_data):
    unique_titles = set()
    filtered = []
    for item in news_data:
        title = item.get("title", "").strip()
        if title and title not in unique_titles:
            unique_titles.add(title)
            filtered.append(item)
    print(f"중복 제거 후 {len(filtered)}개 문서 남음")
    return filtered

# 내용 임베딩
def embed_all_documents(filtered_news, tokenizer, model, device):
    texts = [
        (item.get("title", "") + " " + item.get("text", "")).strip()
        for item in filtered_news
    ]
    embeddings = []
    print("임베딩 생성 중 (CrudeBERT) ...")
    for t in tqdm(texts):
        emb = crudebert_embedding(t, tokenizer, model, device)
        embeddings.append(emb)
    embeddings = np.array(embeddings)
    print(f"임베딩 완료: {embeddings.shape}")
    return embeddings

# 유사도 비교 그룹핑
def build_groups(filtered_news, embeddings, threshold=0.85):
    sim_matrix = cosine_similarity(embeddings)
    groups = []
    visited = set()
    for i in range(len(filtered_news)):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        for j in range(i + 1, len(filtered_news)):
            if sim_matrix[i, j] >= threshold:
                group.append(j)
                visited.add(j)
        groups.append(group)
    print(f"총 {len(groups)}개 그룹 생성됨")
    return groups, sim_matrix

# +UUID
def build_unique_news(filtered_news, groups):
    result = []
    for group in groups:
        rep_idx = max(group, key=lambda idx: len(filtered_news[idx].get("text", "")))
        rep_doc = filtered_news[rep_idx].copy()
        rep_doc["event_uuid"] = str(uuid.uuid4())
        rep_doc["group_size"] = len(group)
        result.append(rep_doc)
    return result

# summary 생성
def create_client(api_key):
    client = OpenAI(api_key=api_key)
    return client
brent_oil_categories = [
    "국제 유가 동향",
    "산유국 정책",
    "공급망 및 원유 생산",
    "수요 및 소비 동향",
    "지정학 및 국제 분쟁",
    "정책, 규제, 에너지 전환",
    "금융, 투자, 환율",
    "기업, 시장 전략",
    "ESG, 환경, 기후",
]
def analyze_article(article_data: dict, client):
    title = article_data.get('title', '')
    url = article_data.get('url', '')
    published = article_data.get('published', '')
    content = article_data.get('content', '').strip()
    group_size = article_data.get('group_size', 1)
    event_uuid = article_data.get('event_uuid', None)
    if not content:
        return {
            "title": title,
            "url": url,
            "published": published,
            "category": "기타",
            "content": content,
            "group_size": group_size,
            "event_uuid": event_uuid,
            "summary": None,
            "sentiment": None,
            "trust": None,
            "relation_nation": []
        }
    content_sample = content[:3000] if len(content) > 3000 else content
    prompt = f"""
당신은 국제 Brent Oil 관련 뉴스를 분석하는 전문가입니다.
아래 기사를 분석하여 반드시 JSON 형식으로만 출력하세요.
카테고리: {", ".join(brent_oil_categories)}
--- 기사 ---
제목: {title}
발행일: {published}
본문: {content_sample}
---
출력 형식:
{{
  "summary": "...",
  "sentiment": {{
        "explanation": "",
        "score": 0.0
  }},
  "category": "",
  "trust": {{
        "explanation": "",
        "score": 0.0,
        "reliable": true
  }},
  "relation_nation": ["국가1", "국가2"]
}}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        analysis = json.loads(response.choices[0].message.content)
        # 안전성 보정 처리
        summary = analysis.get("summary", "") or "요약 정보 없음"
        sentiment = analysis.get("sentiment", {"score": 0.5, "explanation": ""})
        trust = analysis.get("trust", {"score": 0.5, "explanation": "", "reliable": True})
        relation = analysis.get("relation_nation", ["미국"])
        return {
            "title": title,
            "url": url,
            "published": published,
            "category": analysis.get("category", "기타"),
            "content": content,
            "group_size": group_size,
            "event_uuid": event_uuid,
            "summary": summary,
            "sentiment": sentiment,
            "trust": trust,
            "relation_nation": relation,
        }
    except Exception as e:
        print(f":경고: 분석 오류 ({title[:40]}): {e}")
        return {
            "title": title,
            "url": url,
            "published": published,
            "category": "기타",
            "content": content,
            "group_size": group_size,
            "event_uuid": event_uuid,
            "summary": None,
            "sentiment": None,
            "trust": None,
            "relation_nation": []
        }
def analyze_all_articles(articles, client, tqdm_desc="기사 분석 중"):
    results = []
    count_new = 0
    for item in tqdm(articles, desc=tqdm_desc):
        summary = item.get("summary")
        # summary가 없으면 분석
        if summary in [None, "", "null", "요약 정보 없음"]:
            analyzed = analyze_article(item, client)
            results.append(analyzed)
            count_new += 1
        else:
            results.append(item)
    return results, count_new

# 임베딩 관련 함수들
def get_embedding(text, tokenizer, model, max_length=512):
    inputs = tokenizer(
        text,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_length
    )
    with torch.no_grad():
        outputs = model(**inputs)
        embedding = outputs.last_hidden_state[:, 0, :].numpy()  # 768차원
    return embedding[0]

# 차원 축소 설정
input_dim = 768
target_dim = 64
np.random.seed(42)
projection_matrix = np.random.randn(input_dim, target_dim)  # (768, 64)

def reduce_to_64dim(vec768):
    return np.dot(vec768, projection_matrix)

def generate_summary_embeddings(articles, tokenizer, model, output_file):
    embeddings_768 = []
    valid_indices = []
    generated_count = 0
    # --------------------------
    # 1차: summary 있는 row는 즉시 랜덤64차원 변환
    # --------------------------
    for i, article in enumerate(tqdm(articles, desc="1차 변환")):
        summary = article.get("summary", None)
        if not summary:
            article["summary_embedding"] = None
            continue
        try:
            emb768 = get_embedding(summary, tokenizer, model)     # 768차원
            emb64 = reduce_to_64dim(emb768)     # 64차원 변환
            article["summary_embedding"] = emb64.tolist()
            generated_count += 1
        except Exception as e:
            print(f":경고: 임베딩 실패: article {i} — {e}")
            article["summary_embedding"] = None
    # --------------------------
    # 2차: 64차원이 아니거나 None → SVD 재처리 대상으로 모음
    # --------------------------
    for i, article in enumerate(tqdm(articles, desc="검증 및 SVD 준비")):
        emb = article.get("summary_embedding", None)
        if (
            emb in [None, "null", []]
            or not isinstance(emb, list)
            or len(emb) != target_dim
        ):
            try:
                new768 = get_embedding(article["summary"], tokenizer, model)
                embeddings_768.append(new768)
                valid_indices.append(i)
            except:
                article["summary_embedding"] = None
    # --------------------------
    # 3차: SVD로 강제 64차원 맞추기
    # --------------------------
    if len(embeddings_768) > 0:
        print("\n:불: TruncatedSVD를 이용한 강제 차원 축소 수행")
        arr = np.array(embeddings_768)
        svd = TruncatedSVD(n_components=target_dim, random_state=42)
        reduced = svd.fit_transform(arr)
        print(f"설명된 분산 비율: {svd.explained_variance_ratio_.sum():.4f}")
        for idx, row_idx in enumerate(valid_indices):
            articles[row_idx]["summary_embedding"] = reduced[idx].tolist()
            generated_count += 1
    # --------------------------
    # 파일 저장
    # --------------------------
    if output_file:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"\n 결과 저장 완료: {output_file}")
    # --------------------------
    # 최종 결과 반환
    # --------------------------
    return articles, generated_count

def daily_news_data(news_data):

    # 2) 중복 제거
    filtered = remove_duplicate_titles(news_data)

    # 3) CrudeBERT 로드
    tokenizer, model, device = load_crudebert()

    # 4) 본문 임베딩 (내용 + 제목)
    embeddings = embed_all_documents(filtered, tokenizer, model, device)

    # 5) 유사도 기반 그룹 생성
    groups, sim_matrix = build_groups(filtered, embeddings)

    # 6) 그룹 대표 문서 + UUID 부여
    grouped_news = build_unique_news(filtered, groups)

    api_key = os.getenv("OPENAI_API_KEY")

    client = create_client(api_key)

    # 8) summary / sentiment / trust 생성
    analyzed_news, new_count = analyze_all_articles(grouped_news, client)
    print(f"새로 생성된 summary 개수: {new_count}")

    # 9) summary embedding 생성 (768→64)
    output_emb_file = "data/embedded_news.json"
    final_articles, embed_count = generate_summary_embeddings(
        analyzed_news,
        tokenizer,
        model,
        output_emb_file
    )
    print(f"생성된 summary_embedding 개수 = {embed_count}")
    print("전체 처리 완료!")

    
    # os.makedirs("images", exist_ok=True)

    # for idx, article in enumerate(final_articles):
    #     summary = article.get("summary")
    #     if not summary:
    #         continue
    #     save_path = f"repository/data/images/card_{idx}.jpg"
    #     generate_card_news(summary, save_path)
    #     print(f"[{idx}] 카드뉴스 생성 완료 → {save_path}")
        
    # print("\n:짠: 모든 카드뉴스 생성 완료!")

    return final_articles
