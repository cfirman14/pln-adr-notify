"""Pure calculation logic: trigger detection, target allocation, incentive and penalty.

No I/O here, so every function can be unit-tested directly.
"""
from dataclasses import dataclass
import pandas as pd

import config

REQUIRED_SNAPSHOT_COLS = {"timestamp", "substation", "feeder", "s_kva", "s_rated_kva"}
REQUIRED_CUSTOMER_COLS = {"customer_id", "name", "feeder", "email", "flex_kw", "monthly_bill_idr"}


# ---------------------------------------------------------------- loading ---
def load_snapshot(path_or_buffer) -> pd.DataFrame:
    """Feeder snapshot CSV: one row per feeder per interval."""
    df = pd.read_csv(path_or_buffer)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_SNAPSHOT_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Snapshot CSV is missing columns: {sorted(missing)}")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if "duration_h" not in df.columns:
        df["duration_h"] = config.DEFAULT_DURATION_H
    df["duration_h"] = df["duration_h"].fillna(config.DEFAULT_DURATION_H).astype(float)
    return df


def load_customers(path_or_buffer=None) -> pd.DataFrame:
    df = pd.read_csv(path_or_buffer or config.CUSTOMERS_CSV)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_CUSTOMER_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Customer CSV is missing columns: {sorted(missing)}")
    return df


# ---------------------------------------------------------------- trigger ---
def classify(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Add loading %, status and the feeder-level curtailment target.

    Trigger rule   : S >= 0.8 x S_rated                      (paper, Sec. III-A)
    Target rule    : P_target [kW] = Delta S [kVA]           (paper, Eq. 5; conservative)
    """
    df = snapshot.copy()
    df["loading"] = df["s_kva"] / df["s_rated_kva"]
    df["limit_kva"] = config.TRIGGER_LOADING * df["s_rated_kva"]
    df["excess_kva"] = (df["s_kva"] - df["limit_kva"]).clip(lower=0)
    df["status"] = df["loading"].ge(config.TRIGGER_LOADING).map({True: "TRIGGER", False: "NORMAL"})
    df["target_kw"] = df["excess_kva"].where(df["status"] == "TRIGGER", 0.0).round(2)
    return df


# ------------------------------------------------------------- allocation ---
def allocate(feeder_target_kw: float, customers_on_feeder: pd.DataFrame) -> pd.DataFrame:
    """Split a feeder target across its customers, w_j = D_j / sum(D) (paper, Eq. 6)."""
    c = customers_on_feeder.copy()
    total_flex = c["flex_kw"].sum()
    if total_flex <= 0:
        raise ValueError("Customers on this feeder have no flexible load (flex_kw).")
    c["weight"] = c["flex_kw"] / total_flex
    c["target_kw"] = (c["weight"] * feeder_target_kw).round(2)
    return c


# -------------------------------------------------------------- money -------
def incentive_idr(delivered_kw: float, duration_h: float) -> float:
    """Eq. (2): I = 1/3 x C_avoided x P_curtailed x T."""
    return config.INCENTIVE_FACTOR * config.C_AVOIDED * max(delivered_kw, 0) * duration_h


def penalty_idr(shortfall_kw: float, duration_h: float) -> float:
    """Eq. (3): Penalty = dE x 80% x C_avoidable x T, dE = committed - delivered (kW)."""
    return config.PENALTY_FACTOR * max(shortfall_kw, 0) * config.C_AVOIDABLE * duration_h


def committed_kw(target_kw: float, response: str) -> float:
    """kW the customer is committed to, given their response."""
    return {
        "ACCEPT_100": target_kw,
        "AUTO_APPROVED": target_kw,
        "ACCEPT_80": config.PARTIAL_ACCEPT_SHARE * target_kw,
    }.get(response, 0.0)


@dataclass
class Settlement:
    committed_kw: float
    delivered_kw: float
    incentive_idr: float
    penalty_idr: float

    @property
    def net_bill_adjustment_idr(self) -> float:
        """Negative = credit on the bill, positive = charge."""
        return self.penalty_idr - self.incentive_idr


def settle(iteration: int, target_kw: float, response: str,
           delivered_kw: float, duration_h: float) -> Settlement:
    """Settlement rule of the two-iteration workflow (paper, Sec. III-B).

    Iteration 1 -> incentive on verified curtailment, capped at the commitment.
    Iteration 2 -> penalty on any shortfall against the commitment (no incentive).
    Declined    -> neither.
    """
    commit = committed_kw(target_kw, response)
    delivered = max(delivered_kw or 0.0, 0.0)
    if commit == 0:
        return Settlement(0.0, delivered, 0.0, 0.0)
    if iteration == 1:
        return Settlement(commit, delivered, round(incentive_idr(min(delivered, commit), duration_h)), 0.0)
    return Settlement(commit, delivered, 0.0, round(penalty_idr(commit - delivered, duration_h)))
