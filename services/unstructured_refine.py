import numpy as np
import pandas as pd

from models.unstructured_model import (
    load_latest_forward_params,
    compute_news_impact_per_lag,
)


def unstructure_refine(df: pd.DataFrame) -> pd.DataFrame:
    """
    입력:
        - cluster_* 컬럼(동적 K) + 날짜 컬럼(Date 또는 date) + 타깃(있으면)
          brent_close / prod_weekly / wti_mm_net_long 포함된 DataFrame

    처리:
        - 최신 multi-variable 파라미터 로드
        - 일간/주간 데이터 분리
        - BRNT/EIA/COT news impact 계산
        - cluster_* 컬럼 제거

    출력:
        - brent_news_impact_{d}
        - eia_news_impact_{d}
        - cot_news_impact_{d}
        가 추가된 DataFrame
    """
    df = df.copy()

    # ===== 1) 날짜 컬럼 정규화 =====
    date_col = None
    for c in ("date", "Date"):
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        raise ValueError("Date/date 컬럼이 없습니다.")

    df["date"] = pd.to_datetime(df[date_col])
    df = df.sort_values("date").reset_index(drop=True)

    # ===== 2) 파라미터 로드 =====
    params = load_latest_forward_params()
    cluster_cols_param = params["cluster_cols"]
    brent_A = params["brent_A"]
    L_DAY = params["brent_H"]
    eia_A = params["eia_A"]
    L_EIA = params["eia_H"]
    cot_A = params["cot_A"]
    L_COT = params["cot_H"]

    # ===== 3) cluster 정리 =====
    missing = [c for c in cluster_cols_param if c not in df.columns]
    if missing:
        raise ValueError(f"다음 cluster 컬럼이 df에 없습니다: {missing}")

    df[cluster_cols_param] = (
        df[cluster_cols_param]
        .astype(float)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    # ===== 4) 일간/주간 데이터 생성 =====
    df_daily = df.copy()

    df_weekly = (
        df
        .set_index("date")
        .resample("W-FRI")
        .last()
        .reset_index()
    )

    df_weekly[cluster_cols_param] = (
        df_weekly[cluster_cols_param]
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

    # diff 타깃은 refine 단계에서는 직접 쓰지 않지만,
    # weekly cluster 값을 안정적으로 만들기 위해 NaN/inf 정리만 해둔다.
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

    # ===== 5) news impact 계산 =====
    df_features = df_daily.copy()

    # -- (1) Brent: 일간 --
    if brent_A is not None and L_DAY and L_DAY > 0:
        print("[unstructure_refine] BRENT news impact 계산 중...")
        C_daily = df_daily[cluster_cols_param].astype(float).values
        impact_brent = compute_news_impact_per_lag(C_daily, brent_A, L_DAY)

        for l in range(L_DAY):
            col = f"brent_news_impact_{l + 1}"
            df_features[col] = impact_brent[:, l]
    else:
        print("[unstructure_refine] BRENT 파라미터 없음 → 생략")

    # -- (2) EIA: 주간 → 일간 매핑 --
    if eia_A is not None and L_EIA and L_EIA > 0:
        print("[unstructure_refine] EIA news impact 계산 중...")
        C_weekly = df_weekly[cluster_cols_param].astype(float).values
        impact_eia_weekly = compute_news_impact_per_lag(C_weekly, eia_A, L_EIA)

        df_eia_impact_week = df_weekly[["date"]].copy()
        for l in range(L_EIA):
            col = f"eia_news_impact_{l + 1}"
            df_eia_impact_week[col] = impact_eia_weekly[:, l]

        df_features = pd.merge_asof(
            df_features.sort_values("date"),
            df_eia_impact_week.sort_values("date"),
            on="date",
            direction="backward",
        )
    else:
        print("[unstructure_refine] EIA 파라미터 없음 → 생략")

    # -- (3) COT: 주간 → 일간 매핑 --
    if cot_A is not None and L_COT and L_COT > 0:
        print("[unstructure_refine] COT news impact 계산 중...")
        C_weekly = df_weekly[cluster_cols_param].astype(float).values
        impact_cot_weekly = compute_news_impact_per_lag(C_weekly, cot_A, L_COT)

        df_cot_impact_week = df_weekly[["date"]].copy()
        for l in range(L_COT):
            col = f"cot_news_impact_{l + 1}"
            df_cot_impact_week[col] = impact_cot_weekly[:, l]

        df_features = pd.merge_asof(
            df_features.sort_values("date"),
            df_cot_impact_week.sort_values("date"),
            on="date",
            direction="backward",
        )
    else:
        print("[unstructure_refine] COT 파라미터 없음 → 생략")

    # ===== 6) NaN → 0, cluster_* 제거 =====
    impact_like_cols = [
        c for c in df_features.columns
        if c.startswith("brent_news_impact_")
        or c.startswith("eia_news_impact_")
        or c.startswith("cot_news_impact_")
    ]
    for col in impact_like_cols:
        df_features[col] = df_features[col].fillna(0.0)

    cluster_like_cols = [c for c in df_features.columns if c.startswith("cluster_")]
    df_features = df_features.drop(columns=cluster_like_cols, errors="ignore")

    print("[unstructure_refine] 완료")
    return df_features