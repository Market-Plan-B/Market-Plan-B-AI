import os
import glob
from datetime import datetime
from typing import List, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler


REPO_DIR = "app/repository/unstructured_params"
PARAM_PATTERN = "unstructure_model_tensor_fwd_*.npz"


# =========================
# 내부 유틸 (학습용)
# =========================
def _get_cluster_cols(df: pd.DataFrame) -> List[str]:
    """cluster_* 컬럼 자동 추출."""
    cols = [c for c in df.columns if c.startswith("cluster_")]
    cols = sorted(cols, key=lambda x: int(x.split("_")[1]))
    if not cols:
        raise ValueError("cluster_* 컬럼이 없습니다.")
    return cols


# =========================
# 1. FORWARD 학습 함수
#    오늘 cluster → D+1~D+H 타깃
# =========================
def train_unstructured_forward_model(
    df: pd.DataFrame,
    target_col: str = "brent_ret_1d",
    H: int = 5,          # D+1 ~ D+H
    repo_dir: str = REPO_DIR,
) -> str:
    """
    각 horizon h=1..H 에 대해 y_{t+h} ~ cluster[t, :]로 Ridge 회귀를 학습하고,
    A_forward(K, H)를 저장합니다.
    """
    df = df.copy()
    if "Date" in df.columns:
        df = df.sort_values("Date").reset_index(drop=True)

    # 1) cluster 행렬
    cluster_cols = _get_cluster_cols(df)
    C = df[cluster_cols].astype(float)
    C = C.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    C = C.values  
    N, K = C.shape

    if target_col not in df.columns:
        raise ValueError(f"{target_col} 컬럼이 없습니다.")
    y_base = df[target_col].astype(float).values  

    # 2) horizon별 Ridge 학습
    A_forward = np.zeros((K, H), dtype=float)
    alphas = np.logspace(-4, 5, 200)
    alphas_used = np.zeros(H, dtype=float)
    intercepts = np.zeros(H, dtype=float)

    for h in range(1, H + 1):
        # y_{t+h}
        y_shift = pd.Series(y_base).shift(-h).values  
        mask = ~np.isnan(y_shift)
        if mask.sum() < 10:
            raise ValueError(f"h={h} 에 대해 유효한 샘플이 너무 적습니다.")

        X_h = C[mask, :]           
        y_h = y_shift[mask]        

        scaler = StandardScaler()
        X_h_std = scaler.fit_transform(X_h)

        model = RidgeCV(alphas=alphas, scoring="neg_mean_squared_error")
        model.fit(X_h_std, y_h)

        # 표준화 역변환 → 원래 스케일 coef
        coef_std = model.coef_               
        coef_orig = coef_std / scaler.scale_ 

        A_forward[:, h - 1] = coef_orig
        alphas_used[h - 1] = model.alpha_
        intercepts[h - 1] = model.intercept_

        print(f"[train_forward] h={h}, alpha_used={model.alpha_:.6f}")

    # 3) 저장
    os.makedirs(repo_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(repo_dir, f"unstructure_model_tensor_fwd_{today}.npz")

    np.savez(
        out_path,
        A_forward=A_forward,                 
        cluster_cols=np.array(cluster_cols),
        H=np.array([H]),
        target_col=np.array([target_col]),
        alphas=alphas,
        alphas_used=alphas_used,
        intercepts=intercepts,
    )

    print(f"[train_unstructured_forward_model] 저장 완료: {out_path}")
    print(f"  - target_col: {target_col}, K={K}, H={H}")
    return out_path


# =========================
# 2. 최신 FORWARD 파라미터 로드
# =========================
def load_latest_forward_params(
    repo_dir: str = REPO_DIR,
) -> Tuple[np.ndarray, List[str], int, str, str]:
    """
    repo_dir에서 unstructure_model_tensor_fwd_*.npz 중
    가장 최신 파일을 골라 A_forward, cluster_cols, H, path, target_col 반환.
    """
    pattern = os.path.join(repo_dir, PARAM_PATTERN)
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"{pattern} 패턴에 맞는 파일이 없습니다.")

    latest = candidates[-1]
    data = np.load(latest, allow_pickle=True)

    A_forward = data["A_forward"]             
    cluster_cols = list(data["cluster_cols"])
    H = int(data["H"][0])
    target_col = str(data["target_col"][0])

    return A_forward, cluster_cols, H, latest, target_col
