import json
import numpy as np
import pandas as pd
import torch
from captum.attr import IntegratedGradients
import joblib

from app.services.brent_data_pipeline import build_full_dataset
from app.models.bigru_model import BiGRU   

# --------------------------------------------------
# 1) GRU 예측 + 가격 복원
# --------------------------------------------------

def predict_next_day(df, model, scaler, seq_len=30, target_col="brent_ret_5d"):
    """
    df: 전체 feature dataframe
    """
    print(df.head())  # 확인용 제거 해야함
    # return 컬럼 제거 (훈련과 동일 로직)
    ret_cols = [c for c in df.columns if "ret_" in c and c != target_col]
    df_input = df.drop(columns=ret_cols + ["brent_close", "wti_close"], errors="ignore")

    # 스케일링
    df_scaled = scaler.transform(df_input)

    # 마지막 window (seq_len 길이)
    X_last = df_scaled[-seq_len:]               # (seq, dim)
    X_tensor = torch.tensor(X_last[None, :, :], dtype=torch.float32)

    model.eval()
    with torch.no_grad():
        pred_ret = float(model(X_tensor).numpy().reshape(-1)[0])

    today_close = float(df["brent_close"].iloc[-1])
    pred_close = today_close * (1 + pred_ret)

    return pred_ret, today_close, pred_close, X_last


# --------------------------------------------------
# 2) XAI — Integrated Gradients
# --------------------------------------------------

def explain_gru_prediction_ig(model, X_sample, feature_names):
    """
    X_sample: (seq_len, feature_dim)
    """
    model.eval()
    ig = IntegratedGradients(model)

    X_tensor = torch.tensor(X_sample[None, :, :], dtype=torch.float32)

    attributions, delta = ig.attribute(
        X_tensor, n_steps=50, return_convergence_delta=True
    )

    attr = attributions.squeeze(0).detach().numpy()  # (seq, dim)
    importance = np.mean(np.abs(attr), axis=0)

    return [
        {"feature": feature_names[i], "importance": float(importance[i])}
        for i in range(len(feature_names))
    ]


# --------------------------------------------------
# 3) end-to-end inference function
# --------------------------------------------------

def run_inference(
    news_list,
    df,
    model_path=r"app\repository\structured_params\model_weight\bigru_brent_ret5d.pth",
    scaler_path=r"app\repository\structured_params\model_weight\scaler_brent_ret5d.pkl",
    seq_len=30,
    target_horizon=5,
    save_path="app/repository/data/prediction_output.json"
):

    # # ------------------------
    # # (1) 데이터 생성
    # # ------------------------
    # df = build_full_dataset(
    #     news=news_list,
    #     start="2013-09-01",
    #     end=None,               # 오늘까지 자동
    #     target_horizon=target_horizon,
    #     umap_path="model_weight/umap_64to20.model",
    #     kmeans_path="model_weight/kmeans_20d_30clusters.model",
    #     hdbscan_path="model_weight/hdbscan_20d.model"
    # )

    # ------------------------
    # (2) scaler 로드
    # ------------------------
    scaler = joblib.load(scaler_path)

    # ------------------------
    # (3) 모델 정의 → weight 로드
    # ------------------------
    # return 컬럼 제거 후 feature_dim 계산
    ret_cols = [c for c in df.columns if "ret_" in c and c != "brent_ret_5d"]
    df_input = df.drop(columns=ret_cols + ["brent_close", "wti_close"], errors="ignore")
    feature_names = df_input.columns.tolist()

    input_dim = len(feature_names)

    model = BiGRU(input_dim=input_dim, hidden_dim=64, num_layers=1)
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()

    # ------------------------
    # (4) 다음날 예측
    # ------------------------
    pred_ret, today_close, pred_close, X_last = predict_next_day(
        df=df,
        model=model,
        scaler=scaler,
        seq_len=seq_len,
        target_col="brent_ret_5d"
    )

    # ------------------------
    # (5) XAI
    # ------------------------
    xai = explain_gru_prediction_ig(
        model=model,
        X_sample=X_last,
        feature_names=feature_names
    )

    # ------------------------
    # (6) 저장
    # ------------------------
    output = {
        "prediction": {
            "pred_return": pred_ret,
            "today_close": today_close,
            "predicted_next_close": pred_close
        },
        "xai": xai
    }

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output




# if __name__ == "__main__":
#     # 예시: JSON 형태의 뉴스 영향도 데이터 로드
#     with open("./extra_embedded.json", "r", encoding="utf-8") as f:
#         news_list = json.load(f)

#     result = run_inference(
#         news_list=news_list,
#         model_path="model_weight/bigru_ret5d.pth",
#         scaler_path="model_weight/scaler.pkl",
#         save_path="prediction_today.json"
#     )

#     print(result)