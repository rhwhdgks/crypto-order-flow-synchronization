from __future__ import annotations

import numpy as np
import pandas as pd


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    """Return Benjamini-Hochberg FDR-adjusted p-values with index preserved."""
    numeric = pd.to_numeric(p_values, errors="coerce")
    adjusted = pd.Series(np.nan, index=p_values.index, dtype=float)
    valid = numeric.dropna().clip(0.0, 1.0)
    if valid.empty:
        return adjusted

    order = valid.sort_values().index
    ranked = valid.loc[order].to_numpy(dtype=float)
    count = len(ranked)
    raw_adjusted = ranked * count / np.arange(1, count + 1)
    monotone = np.minimum.accumulate(raw_adjusted[::-1])[::-1]
    adjusted.loc[order] = np.clip(monotone, 0.0, 1.0)
    return adjusted
