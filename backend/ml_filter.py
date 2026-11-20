"""Optional, separately gated ML filter using a fixed explainable feature schema."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from .config import DATA_DIR

FEATURE_NAMES = ("momentum", "trend", "volume", "regime", "volatility", "liquidity", "reward_risk")


def feature_vector(factors: dict[str, float]) -> np.ndarray:
    """Stable feature order shared by offline training and autonomous inference."""
    return np.asarray([float(factors.get(name, 0.0)) for name in FEATURE_NAMES], dtype=float)


@dataclass(frozen=True)
class ValidationReport:
    trained: bool
    samples: int
    out_of_sample_auc: float | None
    approved: bool
    reason: str


class MLSignalFilter:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or DATA_DIR / "signal_model.joblib"
        self.validation_path = self.model_path.with_suffix(".validation.json")
        self.model: Any = None
        self.approved = False
        self.validation: ValidationReport | None = None
        self.load()

    def load(self) -> bool:
        """Load only artifacts accompanied by a persisted out-of-sample approval."""
        self.model = None
        self.approved = False
        if not self.model_path.exists() or not self.validation_path.exists():
            return False
        try:
            metadata = json.loads(self.validation_path.read_text())
            if not metadata.get("approved") or tuple(metadata.get("feature_names", ())) != FEATURE_NAMES:
                return False
            import joblib
            model = joblib.load(self.model_path)
            report = ValidationReport(
                True, int(metadata["samples"]), float(metadata["out_of_sample_auc"]), True,
                "Loaded out-of-sample-approved model",
            )
        except Exception:
            return False
        self.model = model
        self.validation = report
        self.approved = True
        return True

    def train(self, features: np.ndarray, outcomes: np.ndarray) -> ValidationReport:
        features = np.asarray(features, dtype=float)
        outcomes = np.asarray(outcomes)
        if features.ndim != 2 or features.shape[1] != len(FEATURE_NAMES):
            return ValidationReport(False, len(features), None, False,
                                    f"Features must have {len(FEATURE_NAMES)} columns: {', '.join(FEATURE_NAMES)}")
        if len(features) < 200 or len(features) != len(outcomes):
            return ValidationReport(False, len(features), None, False, "At least 200 aligned samples are required")
        split = int(len(features) * 0.75)
        if len(np.unique(outcomes[:split])) < 2 or len(np.unique(outcomes[split:])) < 2:
            return ValidationReport(False, len(features), None, False, "Train and holdout sets need both outcome classes")
        model = HistGradientBoostingClassifier(max_iter=120, learning_rate=0.05, max_leaf_nodes=12, random_state=42)
        model.fit(features[:split], outcomes[:split])
        probabilities = model.predict_proba(features[split:])[:, 1]
        auc = float(roc_auc_score(outcomes[split:], probabilities))
        # A fixed usefulness floor; this alone does not establish a durable trading edge.
        approved = auc >= 0.56
        report = ValidationReport(
            True, len(features), auc, approved,
            "Out-of-sample gate passed" if approved else "Out-of-sample AUC below 0.56",
        )
        self.validation = report
        if approved:
            import joblib
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, self.model_path)
            self.validation_path.write_text(json.dumps({
                "approved": True, "samples": len(features), "out_of_sample_auc": auc,
                "feature_names": FEATURE_NAMES, "trained_at": datetime.now(timezone.utc).isoformat(),
            }))
            self.model = model
            self.approved = True
        return report

    def score(self, features: np.ndarray) -> float | None:
        if not self.approved or self.model is None:
            return None
        values = np.asarray(features, dtype=float)
        if values.shape != (len(FEATURE_NAMES),):
            raise ValueError(f"Expected {len(FEATURE_NAMES)} aligned features")
        return float(self.model.predict_proba(values.reshape(1, -1))[0, 1])
