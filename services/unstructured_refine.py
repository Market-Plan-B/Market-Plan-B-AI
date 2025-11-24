
import numpy as np
import pandas as pd

from app.models.unstructured_model import load_latest_forward_params


def unstructure_refine(df: pd.DataFrame) -> pd.DataFrame:
    """
    입력:
        - cluster_* 컬럼(동적 K) + (있으면) Date 컬럼 포함된 DataFrame

    처리:
        - 최신 forward 파라미터(A_forward, cluster_cols, H) 로드
        - 오늘 cluster → D+1~D+H 예상 영향 피처 생성

    출력:
        - news_forward_d{h}_value (h=1..H)
        - news_forward_total 컬럼이 추가된 DataFrame
    """
    df = df.copy()
    if "Date" in df.columns:
        df = df.sort_values("Date").reset_index(drop=True)

    # 1) 최신 forward 파라미터 로드
    A_fwd, cluster_cols, H, used_path, target_col = load_latest_forward_params()

    # 2) cluster_* 존재 여부 체크
    missing: list[str] = [c for c in cluster_cols if c not in df.columns]
    if missing:
        raise ValueError(f"다음 cluster 컬럼이 없습니다: {missing}")

    # 3) raw cluster 행렬 (오늘 기준)
    C = df[cluster_cols].astype(float)
    C = C.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    C = C.values  

    N, K = C.shape
    if K != A_fwd.shape[0]:
        raise ValueError(f"클러스터 개수(K={K})와 A_forward K={A_fwd.shape[0]}가 다릅니다. (param: {used_path})")

    # 4) forward 임팩트 계산
    #    C: (N, K), A_fwd: (K, H) → impact: (N, H)
    impact = C @ A_fwd

    # 5) 컬럼 붙이기: D+1 ~ D+H
    for h in range(1, H + 1):
        col_name = f"news_forward_d{h}_value"
        df[col_name] = impact[:, h - 1]

    df["news_forward_total"] = np.sum(impact, axis=1)

    print(f"[unstructure_refine] 사용 파라미터: {used_path}, target={target_col}")
    return df
