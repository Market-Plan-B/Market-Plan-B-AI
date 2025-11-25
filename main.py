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








