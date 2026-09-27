"""Alpha158 精選因子移植（研究版，V2 观察期产物）——纯 pandas、零新依赖。

本檔是 RESEARCH-ONLY：生產 daemon/rules/scoring 一律不 import。
每個因子 = 一個「事先聲明的方向性假設」（文獻先驗），入場券由 alpha_research.py
用我們自己的 22 只 × 5 年數據（train/validation + 扣基準漂移）裁決。

信息维度与假設（與現有 Layer 2 規則正交）：
  F1 vol_pctl   低波動異象：波動率百分位 ≤20% → 看多（≥80% → 看空）
  F2 pv_corr    量價背離：20日價量相關 ≤ -0.4 → 看多（縮量陰跌=吸籌）
  F3 stoch_k    區間位置：%K ≤ 0.2 → 看多；≥ 0.8 → 看空（高低區間版 RSI）
  F4 high52     52週高點錨定：距離滾動高點 ≤5% → 看多（George & Hwang）
  F5 vwap_dev   VWAP 持續偏離：收盤 ≥ 20日VWAP×1.03 → 看多；≤×0.97 → 看空
"""
import numpy as np
import pandas as pd

WARMUP = 60  # 最小預熱（與現有回測一致）


def realized_vol_state(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """F1：20日已實現波動率在過去一年的百分位。"""
    ret = df["Close"].pct_change()
    vol = ret.rolling(20).std()
    pctl = vol.rolling(252, min_periods=WARMUP).rank(pct=True)
    return pctl <= 0.20, pctl >= 0.80


def pv_corr_state(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """F2：20日價量相關（收盤價 vs 成交量，Pearson）。"""
    corr = df["Close"].rolling(20).corr(df["Volume"])
    bull = corr <= -0.40
    bear = pd.Series(False, index=df.index)  # 單邊假設：只聲明吸籌方向
    return bull.fillna(False), bear


def stoch_k_state(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """F3：20日隨機 %K =（收盤-N日最低）/（N日最高-N日最低）。"""
    lo = df["Low"].rolling(20).min()
    hi = df["High"].rolling(20).max()
    k = (df["Close"] - lo) / (hi - lo).replace(0, np.nan)
    return (k <= 0.20).fillna(False), (k >= 0.80).fillna(False)


def high52_state(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """F4：距滾動一年高點的距離（min_periods=60，短歷史自動降級為半年錨）。"""
    roll_hi = df["Close"].rolling(252, min_periods=WARMUP).max()
    dist = df["Close"] / roll_hi - 1.0
    return (dist >= -0.05).fillna(False), pd.Series(False, index=df.index)


def vwap_dev_state(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """F5：收盤相對 20日 VWAP 的持續偏離。"""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vwap = (tp * df["Volume"]).rolling(20).sum() / df["Volume"].rolling(20).sum().replace(0, np.nan)
    dev = df["Close"] / vwap - 1.0
    return (dev >= 0.03).fillna(False), (dev <= -0.03).fillna(False)


FACTORS = {
    "alpha_lowvol": realized_vol_state,
    "alpha_pvcorr": pv_corr_state,
    "alpha_stochk": stoch_k_state,
    "alpha_high52": high52_state,
    "alpha_vwapdev": vwap_dev_state,
}
