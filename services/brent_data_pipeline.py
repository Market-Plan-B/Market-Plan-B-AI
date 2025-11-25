import yfinance as yf
import pandas as pd
import numpy as np
import joblib
from hdbscan import prediction as hdb_pred

def make_brent_wti_features(start="2013-09-01", end=None, target_horizon=1):
    brent = yf.download("BZ=F", start=start, end=end, auto_adjust=False, progress=False)
    wti   = yf.download("CL=F", start=start, end=end, auto_adjust=False, progress=False)

    brent = brent.rename(columns=str.lower)
    wti   = wti.rename(columns=str.lower)

    df = pd.DataFrame(index=brent.index)
    df["brent_close"] = brent["close"]
    df["wti_close"]   = wti["close"].reindex(df.index)

    # Spread
    df["brent_wti_spread"] = df["brent_close"] - df["wti_close"]

    # Returns (미래 정보 포함 가능성 있음 → 뒤에서 필터링)
    # df["brent_ret_1d"]  = df["brent_close"].pct_change(1)
    df["brent_ret_5d"]  = df["brent_close"].pct_change(5)
    # df["brent_ret_20d"] = df["brent_close"].pct_change(20)

    # df["wti_ret_1d"]  = df["wti_close"].pct_change(1)
    # df["wti_ret_5d"]  = df["wti_close"].pct_change(5)
    # df["wti_ret_20d"] = df["wti_close"].pct_change(20)

    # Moving averages
    df["brent_ma_5"]  = df["brent_close"].rolling(5).mean()
    df["brent_ma_20"] = df["brent_close"].rolling(20).mean()
    df["brent_ma_60"] = df["brent_close"].rolling(60).mean()

    df["wti_ma_5"]  = df["wti_close"].rolling(5).mean()
    df["wti_ma_20"] = df["wti_close"].rolling(20).mean()
    df["wti_ma_60"] = df["wti_close"].rolling(60).mean()

    # Volatility proxies
    df["brent_vol_5d"] = df["brent_close"].pct_change().rolling(5).std()
    df["wti_vol_5d"]   = df["wti_close"].pct_change().rolling(5).std()

    df["high_low_range"] = (brent["high"] - brent["low"]) / brent["close"]

    if target_horizon == 1:
        # 1D 예측은 안전 → 그대로 두기
        pass

    elif target_horizon == 5:
        # 5D 예측 → 어떤 5D/20D return feature도 남아 있으면 안 됨
        drop_cols = [
            c for c in df.columns
            if (
                ("ret_5d" in c or "ret_20d" in c)
                and c != "brent_ret_5d"  # 타겟만 남기기
            )
        ]
        df = df.drop(columns=drop_cols)

    elif target_horizon == 20:
        # 20D 예측 → 5d/20d return 전부 제거 (타겟 제외)
        drop_cols = [
            c for c in df.columns
            if ("ret_5d" in c or "ret_20d" in c)
            and c != "brent_ret_20d"
        ]
        df = df.drop(columns=drop_cols)

    return df.dropna()

def build_full_dataset(
    news: list,
    start="2013-09-01",
    end=None,
    target_horizon=5,
    umap_path=r"app\repository\structured_params\model_weight\umap_64to20.model",
    kmeans_path=r"app\repository\structured_params\model_weight\kmeans_20d_30clusters.model",
    hdbscan_path=r"app\repository\structured_params\model_weight\hdbscan_20d.model",
    max_cluster=30     # KMeans n_clusters
):
    """
    1) 정형 데이터 생성
    2) 뉴스 임베딩 → UMAP → 클러스터링
    3) 날짜 기준 aggregation 후 정형 데이터와 merge
    4) cluster_0 ... cluster_{max_cluster-1} 더미 컬럼 생성
    """

    # -----------------------------------------
    # 1. 정형 데이터
    # -----------------------------------------
    df = make_brent_wti_features(start=start, end=end, target_horizon=target_horizon)
    print(df.head())
    df["date"] = df.index.date   # merge 편하게 date 컬럼 추가

    # -----------------------------------------
    # 2. 모델 로드
    # -----------------------------------------
    umap_model = joblib.load(umap_path)
    kmeans = joblib.load(kmeans_path)
    hdbscan_model = joblib.load(hdbscan_path)

    # -----------------------------------------
    # 3. 뉴스 → DataFrame 변환
    # -----------------------------------------
    # summary_embedding, published or date가 있다고 가정
    news_df = pd.DataFrame(news).copy()

    # 날짜 변환
    news_df["date"] = pd.to_datetime(news_df["published"]).dt.date

    # 임베딩 추출
    embeddings = np.array(news_df["summary_embedding"].tolist())

    # -----------------------------------------
    # 4. UMAP 변환
    # -----------------------------------------
    emb_20d = umap_model.transform(embeddings)

    # -----------------------------------------
    # 5. 클러스터 예측
    # -----------------------------------------
    km_labels = kmeans.predict(emb_20d)

    hdb_labels, hdb_strength = hdb_pred.approximate_predict(hdbscan_model, emb_20d)
    hdb_labels = hdb_labels.astype(int)

    news_df["cluster_km"] = km_labels
    news_df["cluster_hdb"] = hdb_labels
    news_df["hdb_strength"] = hdb_strength

    # -----------------------------------------
    # 6. 날짜 단위로 집계
    # -----------------------------------------
    # 1) 우선 원핫 생성 (실제로 등장한 클러스터만)
    dummies = pd.get_dummies(news_df["cluster_km"])  # prefix 제거

    # 2) 0 ~ max_cluster-1 까지 강제로 전체 클러스터 컬럼 생성
    dummies = dummies.reindex(columns=range(max_cluster), fill_value=0)

    # 3) 컬럼 이름을 cluster_0 ~ cluster_{N-1} 로 변환
    dummies.columns = [f"cluster_{i}" for i in range(max_cluster)]

    # 4) 날짜 넣고 groupby
    news_df_with_dummies = pd.concat([news_df[["date"]], dummies], axis=1)

    daily_cluster = news_df_with_dummies.groupby("date").sum()

    # -----------------------------------------
    # 7. 정형데이터와 merge
    # -----------------------------------------
    df_final = df.merge(daily_cluster, on="date", how="left")
    df_final = df_final.fillna(0)

    # -----------------------------------------
    # 8. 필요 없는 raw 컬럼 제거
    # -----------------------------------------
    df_final = df_final.drop(columns=["date"])

    return df_final