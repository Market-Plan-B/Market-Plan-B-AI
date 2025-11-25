# ./train_unstructured_forward_main.py

import pandas as pd
from services.unstructured_build import build_full_dataset
from models.unstructured_model import train_unstructured_forward_model

def main():
    # 1) 뉴스 받아서 전체 DF 구성 (cluster_0~29 포함)
    #    news 는 평소 쓰시던 리스트 그대로
    news = ...  # 여기만 채우시면 됩니다
    df_full = build_full_dataset(news)

    # 2) forward 모델 학습 + 파라미터 저장
    train_unstructured_forward_model(
        df_full,
        target_col="brent_ret_1d",  # 지금 쓰시는 타깃
        H=5,
    )

if __name__ == "__main__":
    main()
