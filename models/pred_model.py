import numpy as np
import pandas as pd
import xgboost
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score

class PredModel:
    def __init__(self, data: pd.DataFrame):
        self.data = data

    def preprocess(self):
        # 결측치 처리
        self.data = self.data.fillna(self.data.mean())

        # 특징과 타겟 분리
        self.X = self.data.drop(columns=["brent_close"])
        self.y = self.data["brent_close"]

    def train_model(self):
        X_train, X_test, y_train, y_test = train_test_split(
            self.X, self.y, test_size=0.2, random_state=42
        )

        self.model = xgboost.XGBRegressor(objective="reg:squarederror", random_state=42)
        self.model.fit(X_train, y_train)

        y_pred = self.model.predict(X_test)

        self.mse = mean_squared_error(y_test, y_pred)
        self.r2 = r2_score(y_test, y_pred)

    def get_metrics(self):
        return {"MSE": self.mse, "R2": self.r2}