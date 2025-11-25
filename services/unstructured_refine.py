
import numpy as np
import pandas as pd

from app.models.unstructured_model import load_latest_forward_params

def unstructure_refine(df: pd.DataFrame) -> pd.DataFrame:
    """
    입력:
        - cluster_* 컬럼(동적 K) + (있으면) Date 컬럼 포함된 DataFrame

    처리:
        - 최신 forward 파라미터(A_forward, cluster_cols, H) 로드
        - cluster_*로 forward 임팩트 계산
        - 계산 끝난 뒤 cluster_* 컬럼 제거

    출력:
        - news_impact_brent_day{0..H-1}_value
        - news_impact_brent_total 컬럼 추가 (이름을 예전 스케일러에 맞춤)
    """
    df = df.copy()
    if "Date" in df.columns:
        df = df.sort_values("Date").reset_index(drop=True)

    # 1) 최신 forward 파라미터 로드
    A_fwd, cluster_cols, H, used_path, target_col = load_latest_forward_params()

    # 2) 필요한 cluster_* 컬럼이 df에 있는지 확인
    missing = [c for c in cluster_cols if c not in df.columns]
    if missing:
        raise ValueError(f"다음 cluster 컬럼이 df에 없습니다: {missing}")

    # 3) raw cluster 행렬
    C = df[cluster_cols].astype(float)
    C = C.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    C = C.values   # (N, K)

    N, K = C.shape
    if K != A_fwd.shape[0]:
        raise ValueError(
            f"클러스터 개수(K={K})와 A_forward K={A_fwd.shape[0]}가 다릅니다. (param: {used_path})"
        )

    # 4) forward 임팩트 계산: C(N,K) @ A_fwd(K,H) → impact(N,H)
    impact = C @ A_fwd

    # 5) 예전 스케일러/모델이 기대하는 이름으로 컬럼 붙이기
    #    news_impact_brent_day0_value ~ day{H-1}_value
    for h in range(H):
        col_name = f"news_impact_brent_day{h}_value"
        df[col_name] = impact[:, h]

    df["news_impact_brent_total"] = impact.sum(axis=1)

    # 6) cluster_* 컬럼은 최종적으로 제거
    cluster_cols_in_df = [c for c in df.columns if c.startswith("cluster_")]
    df = df.drop(columns=cluster_cols_in_df, errors="ignore")

    print(f"[unstructure_refine] 사용 파라미터: {used_path}, target={target_col}")
    return df

