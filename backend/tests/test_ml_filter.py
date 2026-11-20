import numpy as np

from backend.ml_filter import FEATURE_NAMES, MLSignalFilter, feature_vector


def test_unapproved_ml_model_cannot_score(tmp_path):
    model = MLSignalFilter(tmp_path / "model.joblib")
    assert model.score(feature_vector({name: 0.8 for name in FEATURE_NAMES})) is None


def test_ml_training_requires_holdout_quality_and_persists_approval(tmp_path):
    outcomes = np.arange(240) % 2
    features = np.zeros((240, len(FEATURE_NAMES)))
    features += np.linspace(0, 0.1, len(FEATURE_NAMES))
    features[outcomes == 1] += 2.0
    path = tmp_path / "model.joblib"
    model = MLSignalFilter(path)
    report = model.train(features, outcomes)
    assert report.trained and report.approved
    assert report.out_of_sample_auc >= 0.56
    restored = MLSignalFilter(path)
    assert restored.approved
    assert 0 <= restored.score(feature_vector({name: 0.8 for name in FEATURE_NAMES})) <= 1
