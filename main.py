from app.services.brent_data_pipeline import build_full_dataset
from app.services.unstructured_refine import unstructure_refine
from app.services.pipeline_inference import run_inference
from app.services.unstructured_summary import daily_news_data

def db_load():
    """
    나중에 db 로드해서 사용할 함수 
    지금은 임시로 파일에서 가져오는 것으로

    """
    import os
    import json
    load_path = "app/repository/data/news"

    # 해당 폴더에서 파일 하나 가져와서 json으로
    files = [f for f in os.listdir(load_path) if os.path.isfile(os.path.join(load_path, f))]
    first_file = files[0]

    # 파일 로드
    file_path = os.path.join(load_path, first_file)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data

def daily_news(news):
    news_list = daily_news_data(news)

    return news_list

def daily_modeling(news_list, date):
    """
    하루치 가져와서 데이터 만들고 모델 돌리는 함수

    output = {
        "prediction": {
            "pred_return": pred_ret,
            "today_close": today_close,
            "predicted_next_close": pred_close
        },
        "xai": xai
    }

    """
    df = build_full_dataset(news = news_list, start = date)

    df_refine = unstructure_refine(df)

    output = run_inference(news_list= news_list, df = df_refine)

    return output


data = db_load()
news_list = daily_news(data)
date = "2025-11-18"


result = daily_modeling(news_list,date)





# ===============================================================================================
# ============================================ 대응책 ============================================
# ===============================================================================================

from app.nodes.actiongenerator import actiongenerator
from app.nodes.reportgenerator import reportgenerator
from datetime import datetime, timedelta
import pandas as pd
import json
from dotenv import load_dotenv

def build_compact_news_list(unstructured_data, max_news=5):
    compact_list = []

    for idx, item in enumerate(unstructured_data[:max_news], start=1):
        title = item.get('title', 'N/A')
        summary = item.get('summary', 'N/A')
        sentiment = item.get('sentiment', {}).get('score', 'N/A')
        trust = item.get('trust', {}).get('score', 'N/A')

        compact = (
            f'[뉴스 {idx}] '
            f'제목: {title} | '
            f'요약: {summary} | '
            f'영향도: {sentiment} | '
            f'신뢰도: {trust} | '
            f'본문 일부: {item.get("content")[:600]}'
        )

        compact_list.append(compact)
    compact_list = "\n".join(compact_list)
    return compact_list


# 대응책 날짜 설정해야됨
date = '2025-11-19'

d = datetime.strptime(date, "%Y-%m-%d")
prev_date = d - timedelta(days=1)

date_str = d.strftime("%Y-%m-%d")
prev_date_str = prev_date.strftime("%Y-%m-%d")

# 그래서 오늘 어제꺼만 들어감 
structured_data = pd.read_csv('data/llm_input_sample.csv') ## 여기에 모델 추론에 들어가는 데이터 데려오면 됨
filtered = structured_data[
    structured_data["Date"].isin([date_str, prev_date_str])
]

# 여기는 모델이 추론한 값 + xai 값 데려와야댐
with open('data/prediction_2025_11_19.json', 'r', encoding='utf-8') as f:
    xai_result=json.load(f)

model_prediction=xai_result['prediction']
xai=xai_result['xai']

# 얜 오늘 뉴스 들어온거 summary 된 것
with open('data/extra_embedded (1).json', 'r', encoding='utf-8') as f:
    unstructured_data = json.load(f)

news = build_compact_news_list(unstructured_data, max_news=5)

action=actiongenerator(date=date,
                       structured_data=filtered, 
                       model_prediction=model_prediction, 
                       xai_result=xai, 
                       unstructured_data=news)

print(action)
parsed_json = json.loads(action)


# 대응책 저장
with open("action_output.json", "w", encoding="utf-8") as f:
    json.dump(parsed_json, f, ensure_ascii=False, indent=2)



# ===============================================================================================
# ======================================== daily report =========================================
# ===============================================================================================

#위에꺼 + 대응책 넣어주는 것임
minimal_strategies = {
    "strategies": [
        {
            "name": s["name"],
            "horizon": s["horizon"],
            "objective": s["objective"],
            "actions": s["actions"],
            "data_evidence": s["data_evidence"],
            "risk_note": s["risk_note"],
        }
        for s in action["strategies"]
    ]
}

minimal_strategies_str = json.dumps(minimal_strategies, ensure_ascii=False, indent=2)

report = reportgenerator(
    date=date,
    structured_data=filtered,
    model_prediction=model_prediction,
    xai_result=xai,
    unstructured_data=news,
    precomputed_strategies=minimal_strategies_str
)

print(report)
parsed_json = json.loads(report)

with open("daily_report.html", "w", encoding="utf-8") as f:
    f.write(report)
