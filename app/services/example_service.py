"""Example business-logic service."""

import pandas as pd


def compute_summary(data: list[dict]) -> dict:
    """Return a simple pandas-based summary of a list of dicts."""
    if not data:
        return {"count": 0}
    df = pd.DataFrame(data)
    return {
        "count": len(df),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
    }
