import os
import glob
from datetime import datetime
from typing import List, Dict, Any

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler


REPO_DIR = "app/ai/repository/unstructured_params"
PARAM_PATTERN = "unstructure_model_tensor_fwd_*.npz"

# 날짜 컬럼 후보
DATE_COL_CANDIDATES = ["date", "Date"]

# lag 후보
CANDIDATE_L_DAY = [3, 4, 5, 7, 10]   # Brent 일간
CANDIDATE_L_WEEK = [3, 5, 7, 10]     # EIA/COT 주간

MIN_SAMPLES_BRENT = 100
MIN_SAMPLES_WEEKLY = 30


# =========================
# 내부 유틸
# =========================
def _get_cluster_cols(df: pd.DataFrame) -> List[str]:
    """cluster_* 컬럼 자동 추출."""
    cols = [c for c in df.columns if c.startswith("cluster_")]
    cols = sorted(cols, key=lambda x: int(x.split("_")[1]))
    if not cols:
        raise ValueError("cluster_* 컬럼이 없습니다.")
    return cols


def build_lag_matrix(C_mat: np.ndarray, L: int) -> np.ndarray:
    """
    C_mat: (N, K)  클러스터 시계열
    L    : 시차 길이
    반환: (T, K*L)
        X[t, l*K:(l+1)*K] = C_{t-l, :}
        t=0..T-1, 실제 시점은 (L-1) ~ (N-1)
    """
    N, K = C_mat.shape
    T = N - (L - 1)
    if T <= 0:
        raise ValueError(f"데이터 길이 부족: N={N}, L={L}")

    X = np.zeros((T, K * L), dtype=float)
    for l in range(L):
        X[:, l * K:(l + 1) * K] = C_mat[(L - 1 - l):(L - 1 - l + T), :]
    return X


def _select_best_lag_by_ic(
    df_src: pd.DataFrame,
    cluster_cols: list,
    target_col: str,
    L_candidates: list,
    min_samples: int = 50,
    label: str = "",
):
    """
    여러 lag 후보(L_candidates)에 대해
    70/30 hold-out에서 Pearson IC(상관계수)를 계산하고,
    |IC|가 가장 큰 lag를 선택한다.

    반환:
      best_L     : 최적 lag (int or None)
      best_ic    : 해당 lag에서의 IC (float or None)
      best_A     : (K, L) 계수 텐서 or None
    """
    if target_col not in df_src.columns:
        print(f"[{label}] {target_col} 컬럼 없음 → 스킵")
        return None, None, None

    df_src = df_src.copy().sort_values("date").reset_index(drop=True)

    C = df_src[cluster_cols].astype(float).values   # (N, K)
    y_all = (
        df_src[target_col]
        .astype(float)
        .replace([np.inf, -np.inf], np.nan)
        .ffill()
        .bfill()
        .values
    )

    N, K = C.shape
    best_L = None
    best_score = -np.inf
    best_ic = None
    best_A = None

    for L in L_candidates:
        if N <= L:
            print(f"[{label}] L={L} → 데이터 길이 부족 (N={N}) → 스킵")
            continue

        X_full = build_lag_matrix(C, L)         # (T, K*L)
        T = X_full.shape[0]
        y = y_all[(L - 1):(L - 1 + T)]          # (T,)

        mask = np.isfinite(y)
        X = X_full[mask]
        y = y[mask]

        if len(y) < max(min_samples, 30):
            print(f"[{label}] L={L} → 유효 샘플 부족 ({len(y)}) → 스킵")
            continue

        split = int(len(y) * 0.7)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        scaler = StandardScaler()
        X_train_std = scaler.fit_transform(X_train)
        X_test_std = scaler.transform(X_test)

        alphas = np.logspace(-3, 4, 100)
        model = RidgeCV(alphas=alphas, cv=5)
        model.fit(X_train_std, y_train)

        y_pred = model.predict(X_test_std)

        if np.std(y_test) == 0 or np.std(y_pred) == 0:
            ic = 0.0
        else:
            ic_matrix = np.corrcoef(y_test, y_pred)
            ic = float(ic_matrix[0, 1])

        score = abs(ic)
        print(f"[{label}] L={L} → IC={ic:.4f}, |IC|={score:.4f}")

        coef_std = model.coef_        # (K*L,)
        scale = scaler.scale_         # (K*L,)
        coef_orig = coef_std / scale  # (K*L,)
        A = coef_orig.reshape(L, K).T # (K, L)

        if score > best_score:
            best_score = score
            best_ic = ic
            best_L = L
            best_A = A

    if best_L is None:
        print(f"[{label}] 유효한 lag 후보가 없습니다.")
        return None, None, None

    print(f"[{label}] 최적 lag={best_L}, IC={best_ic:.4f}, |IC|={best_score:.4f}")
    return best_L, best_ic, best_A


def compute_news_impact_per_lag(
    C_mat: np.ndarray,
    A_tensor: np.ndarray,
    L: int,
) -> np.ndarray:
    """
    C_mat   : (N, K)  클러스터 시계열
    A_tensor: (K, L)  Ridge에서 얻은 계수 텐서
    반환    : (N, L)  각 시점 t에서 시차별 임팩트 값
              impact[t, l] = sum_k C[t-l, k] * A[k, l]
              t < l 인 구간은 0으로 처리
    """
    if A_tensor.size == 0 or L <= 0:
        return np.zeros((C_mat.shape[0], 0), dtype=float)

    N, K = C_mat.shape
    impact = np.zeros((N, L), dtype=float)

    for t in range(N):
        for l in range(L):
            if t - l < 0:
                impact[t, l] = 0.0
                continue
            c_vec = C_mat[t - l, :]   # (K,)
            a_vec = A_tensor[:, l]    # (K,)
            impact[t, l] = float(np.dot(c_vec, a_vec))

    return impact


# =========================
# 1. FORWARD 학습 함수
#    (기존 함수명/인자 유지)
# =========================
def train_unstructured_forward_model(
    df: pd.DataFrame,
    target_col: str = "brent_close",
    H: int = 5,
    repo_dir: str = REPO_DIR,
) -> str:
    """
    기존: Brent 단일 타깃 forward 모델
    변경: Brent/EIA/COT 3개 타깃에 대해
          IC 기반 lag 선택 + Ridge 계수 텐서 저장.

    - 함수명/인자는 그대로 유지 (main 수정 불필요)
    - 실제로는 target_col/H 는 사용하지 않고,
      df 안의 brent_close / prod_weekly / wti_mm_net_long 를 자동 인식
    """
    df = df.copy()

    # 날짜 컬럼 정규화
    date_col = None
    for c in DATE_COL_CANDIDATES:
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        raise ValueError(f"날짜 컬럼({DATE_COL_CANDIDATES})이 없습니다.")

    df["date"] = pd.to_datetime(df[date_col])
    df = df.sort_values("date").reset_index(drop=True)

    # 클러스터 컬럼
    cluster_cols = _get_cluster_cols(df)
    df[cluster_cols] = (
        df[cluster_cols]
        .astype(float)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    # 타깃 존재 여부
    has_brent = "brent_close" in df.columns
    has_eia = "prod_weekly" in df.columns
    has_cot = "wti_mm_net_long" in df.columns

    if not any([has_brent, has_eia, has_cot]):
        raise ValueError("brent_close / prod_weekly / wti_mm_net_long 중 아무 것도 없습니다.")

    # 일간 / 주간 데이터
    df_daily = df.copy()
    df_weekly = (
        df
        .set_index("date")
        .resample("W-FRI")
        .last()
        .reset_index()
    )

    df_weekly[cluster_cols] = (
        df_weekly[cluster_cols]
        .astype(float)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    for col in ["brent_close", "prod_weekly", "wti_mm_net_long"]:
        if col in df_weekly.columns:
            df_weekly[col] = (
                df_weekly[col]
                .astype(float)
                .replace([np.inf, -np.inf], np.nan)
                .ffill()
                .bfill()
            )

    # EIA/COT diff 타깃 생성
    if "prod_weekly" in df_weekly.columns:
        df_weekly["prod_weekly_diff"] = df_weekly["prod_weekly"].diff()
        df_weekly["prod_weekly_diff"] = (
            df_weekly["prod_weekly_diff"]
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )

    if "wti_mm_net_long" in df_weekly.columns:
        df_weekly["wti_mm_net_long_diff"] = df_weekly["wti_mm_net_long"].diff()
        df_weekly["wti_mm_net_long_diff"] = (
            df_weekly["wti_mm_net_long_diff"]
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )

    brent_target_col = "brent_close" if has_brent else None
    eia_target_col = "prod_weekly_diff" if "prod_weekly_diff" in df_weekly.columns else None
    cot_target_col = "wti_mm_net_long_diff" if "wti_mm_net_long_diff" in df_weekly.columns else None

    # === Brent (일간) ===
    A_b = None
    L_DAY = None
    if brent_target_col is not None:
        print("\n=== [BRENT] 일간 lag 선택 (IC 기준) ===")
        L_DAY, ic_b, A_b = _select_best_lag_by_ic(
            df_daily,
            cluster_cols,
            brent_target_col,
            L_candidates=CANDIDATE_L_DAY,
            min_samples=MIN_SAMPLES_BRENT,
            label="BRENT",
        )

    # === EIA (주간) ===
    A_e = None
    L_EIA = None
    if eia_target_col is not None:
        print("\n=== [EIA] 주간 lag 선택 (IC 기준) ===")
        L_EIA, ic_e, A_e = _select_best_lag_by_ic(
            df_weekly,
            cluster_cols,
            eia_target_col,
            L_candidates=CANDIDATE_L_WEEK,
            min_samples=MIN_SAMPLES_WEEKLY,
            label="EIA",
        )

    # === COT (주간) ===
    A_c = None
    L_COT = None
    if cot_target_col is not None:
        print("\n=== [COT] 주간 lag 선택 (IC 기준) ===")
        L_COT, ic_c, A_c = _select_best_lag_by_ic(
            df_weekly,
            cluster_cols,
            cot_target_col,
            L_candidates=CANDIDATE_L_WEEK,
            min_samples=MIN_SAMPLES_WEEKLY,
            label="COT",
        )

    # === 파라미터 저장 ===
    os.makedirs(repo_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(repo_dir, f"unstructure_model_tensor_fwd_{today}.npz")

    np.savez(
        out_path,
        cluster_cols=np.array(cluster_cols),
        # BRENT
        brent_A=A_b if A_b is not None else np.empty((0, 0)),
        brent_H=np.array([L_DAY if L_DAY is not None else 0]),
        brent_target=np.array([brent_target_col or ""]),
        # EIA
        eia_A=A_e if A_e is not None else np.empty((0, 0)),
        eia_H=np.array([L_EIA if L_EIA is not None else 0]),
        eia_target=np.array([eia_target_col or ""]),
        # COT
        cot_A=A_c if A_c is not None else np.empty((0, 0)),
        cot_H=np.array([L_COT if L_COT is not None else 0]),
        cot_target=np.array([cot_target_col or ""]),
    )

    print(f"[train_unstructured_forward_model] 저장 완료: {out_path}")
    print(f"  - cluster_cols: {len(cluster_cols)}개")
    print(f"  - Brent lag: {L_DAY}, EIA lag: {L_EIA}, COT lag: {L_COT}")
    return out_path


# =========================
# 2. 최신 파라미터 로드
#    (기존 함수명 유지, 리턴을 dict로)
# =========================
def load_latest_forward_params(
    repo_dir: str = REPO_DIR,
) -> Dict[str, Any]:
    """
    repo_dir에서 unstructure_model_tensor_fwd_*.npz 중
    가장 최신 파일을 골라 multi-variable 파라미터를 반환.

    리턴: dict
      {
        "cluster_cols": [...],
        "brent_A": ndarray or None,
        "brent_H": int,
        "brent_target": str,
        "eia_A": ...,
        "eia_H": ...,
        "eia_target": ...,
        "cot_A": ...,
        "cot_H": ...,
        "cot_target": ...,
        "path": "파일경로",
      }
    """
    pattern = os.path.join(repo_dir, PARAM_PATTERN)
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"{pattern} 패턴에 맞는 파일이 없습니다.")

    latest = candidates[-1]
    data = np.load(latest, allow_pickle=True)

    # 공통
    cluster_cols = list(data["cluster_cols"]) if "cluster_cols" in data.files else []

    # 1) 구버전: A_forward 하나만 있는 경우
    if "A_forward" in data.files:
        A_forward = data["A_forward"]
        H = int(data["H"][0])
        target_col = str(data["target_col"][0])

        return {
            "cluster_cols": cluster_cols,
            "brent_A": A_forward,
            "brent_H": H,
            "brent_target": target_col,
            "eia_A": None,
            "eia_H": 0,
            "eia_target": "",
            "cot_A": None,
            "cot_H": 0,
            "cot_target": "",
            "path": latest,
        }

    # 2) 신규 multi-variable 포맷
    def _get_or_empty(name: str):
        if name in data.files:
            return data[name]
        return np.empty((0, 0))

    brent_A = _get_or_empty("brent_A")
    eia_A = _get_or_empty("eia_A")
    cot_A = _get_or_empty("cot_A")

    brent_H = int(data["brent_H"][0]) if "brent_H" in data.files else 0
    eia_H = int(data["eia_H"][0]) if "eia_H" in data.files else 0
    cot_H = int(data["cot_H"][0]) if "cot_H" in data.files else 0

    brent_target = str(data["brent_target"][0]) if "brent_target" in data.files else ""
    eia_target = str(data["eia_target"][0]) if "eia_target" in data.files else ""
    cot_target = str(data["cot_target"][0]) if "cot_target" in data.files else ""

    return {
        "cluster_cols": cluster_cols,
        "brent_A": brent_A if brent_A.size > 0 else None,
        "brent_H": brent_H,
        "brent_target": brent_target,
        "eia_A": eia_A if eia_A.size > 0 else None,
        "eia_H": eia_H,
        "eia_target": eia_target,
        "cot_A": cot_A if cot_A.size > 0 else None,
        "cot_H": cot_H,
        "cot_target": cot_target,
        "path": latest,
    }
