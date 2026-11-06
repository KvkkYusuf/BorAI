"""FAZ-11 model artifact kayıt, seed sabitleme ve izlenebilirlik testleri."""

import random
from pathlib import Path

import numpy as np
import pytest
import torch

from stock_analysis.models.architectures import (
    CNN1DConfig,
    MLPConfig,
    ModelArchitectureType,
    SmallCNN1D,
    SmallMLP,
)
from stock_analysis.models.registry import (
    ModelArtifactManager,
    ModelIntegrityError,
    ModelNotFoundError,
    ModelSecurityError,
    compute_file_sha256,
    seed_everything,
)


def test_seed_everything_deterministic() -> None:
    """seed_everything ile Python, NumPy ve PyTorch rastgeleliklerinin
    deterministik olduğunu doğrular.
    """
    seed_everything(seed=1337)
    py_rand1 = [random.random() for _ in range(5)]
    np_rand1 = np.random.randn(5)
    torch_rand1 = torch.randn(5)

    seed_everything(seed=1337)
    py_rand2 = [random.random() for _ in range(5)]
    np_rand2 = np.random.randn(5)
    torch_rand2 = torch.randn(5)

    assert py_rand1 == py_rand2
    assert np.allclose(np_rand1, np_rand2)
    assert torch.allclose(torch_rand1, torch_rand2)


def test_save_and_load_mlp_artifact(tmp_path: Path) -> None:
    """SmallMLP modelinin kaydedilmesi ve yeniden yüklenmesinde
    ağırlık ve çıktının korunduğunu test eder.
    """
    manager = ModelArtifactManager(base_dir=tmp_path / "models")

    cfg = MLPConfig(input_dim=10, hidden_dims=(32, 16), output_dim=1)
    model = SmallMLP(cfg)

    model.eval()
    with torch.no_grad():
        dummy_input = torch.randn(4, 10)
        expected_output = model(dummy_input)

    metrics = {"val_loss": 0.0125, "directional_accuracy": 0.58}
    hyperparams = {"lr": 1e-3, "batch_size": 32, "epochs": 20}

    artifact_dir, record = manager.save_artifact(
        model=model,
        model_id="mlp-bist-100-v1",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="bist_2020_2025_sha256_abcdef",
        data_split_id="split-2024-q1",
        feature_names=[f"feat_{i}" for i in range(10)],
        target_type="percentage_return",
        target_horizon="1d",
        hyperparameters=hyperparams,
        metrics=metrics,
        tags=["mlp", "daily", "experimental"],
        notes="İlk baseline geçen MLP modeli",
    )

    assert artifact_dir.exists()
    assert (artifact_dir / "weights.pt").exists()
    assert (artifact_dir / "metadata.json").exists()

    # Meta verinin doğruluğunu kontrol et
    meta = manager.get_metadata("mlp-bist-100-v1")
    assert meta.model_id == "mlp-bist-100-v1"
    assert meta.seed == 42
    assert meta.data_version == "bist_2020_2025_sha256_abcdef"
    assert meta.metrics["directional_accuracy"] == 0.58
    assert len(meta.feature_names) == 10
    assert meta.weights_sha256 == compute_file_sha256(artifact_dir / "weights.pt")

    # Modeli otomatik yeniden yükle
    loaded_model, loaded_record = manager.load_artifact("mlp-bist-100-v1")
    assert isinstance(loaded_model, SmallMLP)
    assert loaded_record.model_id == meta.model_id

    # Çıktıların birebir aynı olduğunu doğrula
    with torch.no_grad():
        loaded_output = loaded_model(dummy_input)
    assert torch.allclose(expected_output, loaded_output, atol=1e-6)


def test_save_and_load_cnn1d_artifact(tmp_path: Path) -> None:
    """SmallCNN1D modelinin kaydedilmesi ve yüklenmesini test eder."""
    manager = ModelArtifactManager(base_dir=tmp_path / "models")

    cfg = CNN1DConfig(in_channels=5, seq_len=20, num_filters=(16, 32), output_dim=1)
    model = SmallCNN1D(cfg)

    model.eval()
    with torch.no_grad():
        dummy_input = torch.randn(2, 5, 20)
        expected_output = model(dummy_input)

    _, record = manager.save_artifact(
        model=model,
        model_id="cnn1d-model-01",
        arch_type=ModelArchitectureType.SMALL_CNN_1D,
        arch_config=cfg,
        seed=101,
        data_version="data_v2_clean",
        metrics={"test_loss": 0.045},
    )

    loaded_model, loaded_record = manager.load_artifact("cnn1d-model-01")
    assert isinstance(loaded_model, SmallCNN1D)
    assert loaded_record.seed == 101

    with torch.no_grad():
        loaded_output = loaded_model(dummy_input)
    assert torch.allclose(expected_output, loaded_output, atol=1e-6)


def test_tampered_weights_raises_integrity_error(tmp_path: Path) -> None:
    """Ağırlık dosyası tahrif edilirse ModelIntegrityError fırlatıldığını doğrular."""
    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    artifact_dir, record = manager.save_artifact(
        model=model,
        model_id="tamper-check-model",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="v1",
    )

    weights_file = artifact_dir / "weights.pt"
    # Ağırlık dosyasının sonuna birkaç sahte bayt ekle
    with weights_file.open("ab") as f:
        f.write(b"tampered_data")

    with pytest.raises(ModelIntegrityError, match="SHA-256 özeti eşleşmiyor"):
        manager.load_artifact("tamper-check-model", verify_integrity=True)


def test_security_path_traversal_prevention(tmp_path: Path) -> None:
    """Güvensiz model_id veya weights_filename verildiğinde
    ModelSecurityError fırlatıldığını doğrular.
    """
    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    with pytest.raises(ModelSecurityError):
        manager.save_artifact(
            model=model,
            model_id="../../etc/passwd",
            arch_type=ModelArchitectureType.SMALL_MLP,
            arch_config=cfg,
            seed=42,
            data_version="v1",
        )

    with pytest.raises(ModelSecurityError):
        manager.save_artifact(
            model=model,
            model_id="valid_model_id",
            arch_type=ModelArchitectureType.SMALL_MLP,
            arch_config=cfg,
            seed=42,
            data_version="v1",
            weights_filename="../evil.pt",
        )


def test_model_not_found(tmp_path: Path) -> None:
    """Kayıtlı olmayan model_id sorgulandığında ModelNotFoundError fırlatıldığını doğrular."""
    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    with pytest.raises(ModelNotFoundError):
        manager.get_metadata("non-existent-model")

    with pytest.raises(ModelNotFoundError):
        manager.load_artifact("non-existent-model")


def test_list_and_get_best_model(tmp_path: Path) -> None:
    """Modellerin listelenmesi ve metriğe göre en iyi modelin seçilmesini test eder."""
    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    manager.save_artifact(
        model=model,
        model_id="model-m1",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=1,
        data_version="v1",
        target_type="percentage_return",
        target_horizon="1d",
        metrics={"mae": 0.05, "accuracy": 0.52},
    )

    manager.save_artifact(
        model=model,
        model_id="model-m2",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=2,
        data_version="v1",
        target_type="percentage_return",
        target_horizon="1d",
        metrics={"mae": 0.02, "accuracy": 0.61},
    )

    manager.save_artifact(
        model=model,
        model_id="model-m3-other-horizon",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=3,
        data_version="v1",
        target_type="percentage_return",
        target_horizon="5d",
        metrics={"mae": 0.01, "accuracy": 0.65},
    )

    records = manager.list_records()
    assert len(records) == 3

    # MAE için en küçük (higher_is_better=False), horizon 1d
    best_mae = manager.get_best_model("mae", higher_is_better=False, target_horizon="1d")
    assert best_mae is not None
    assert best_mae.model_id == "model-m2"
    assert best_mae.metrics["mae"] == 0.02

    # Accuracy için en büyük (higher_is_better=True), horizon 1d
    best_acc = manager.get_best_model("accuracy", higher_is_better=True, target_horizon="1d")
    assert best_acc is not None
    assert best_acc.model_id == "model-m2"

    # Olmayan metrik None dönmeli
    assert manager.get_best_model("sharpe") is None


def test_delete_artifact(tmp_path: Path) -> None:
    """Artifact silme işlevini test eder."""
    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    manager.save_artifact(
        model=model,
        model_id="to-delete",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="v1",
    )

    assert len(manager.list_records()) == 1
    assert manager.delete_artifact("to-delete") is True
    assert len(manager.list_records()) == 0
    assert not (tmp_path / "models" / "to-delete").exists()

    # Tekrar silmeye çalışıldığında False dönmeli
    assert manager.delete_artifact("to-delete") is False


def test_normalizer_and_feature_def_serialization() -> None:
    """Normalizer ve özellik tanımlarının sözlük serileştirmesini test eder."""
    from stock_analysis.models.features import FeatureDefinition, FeatureType, PointInTimeNormalizer

    # 1. FeatureDefinition
    feat_def = FeatureDefinition(
        name="log_ret_5d",
        feature_type=FeatureType.LOG_RETURN,
        window_bars=5,
        lag=1,
        description="5 günlük getiri",
    )
    d = feat_def.to_dict()
    restored_feat = FeatureDefinition.from_dict(d)
    assert restored_feat.name == feat_def.name
    assert restored_feat.feature_type == feat_def.feature_type
    assert restored_feat.window_bars == feat_def.window_bars
    assert restored_feat.lag == feat_def.lag

    # 2. PointInTimeNormalizer
    train_data = [[10.0, 100.0], [20.0, 200.0], [30.0, 300.0]]
    norm = PointInTimeNormalizer(method="standard")
    norm.fit(train_data)

    norm_dict = norm.to_dict()
    assert norm_dict["is_fitted"] is True
    assert norm_dict["fitted_samples"] == 3
    assert norm_dict["means"] == [20.0, 200.0]

    restored_norm = PointInTimeNormalizer.from_dict(norm_dict)
    assert restored_norm.is_fitted is True
    assert restored_norm.means == norm.means
    assert restored_norm.stds == norm.stds


def test_save_and_load_bundled_model(tmp_path: Path) -> None:
    """Modelin normalizasyon parametreleriyle paketlenip yüklenmesini ve tahminini test eder."""
    from stock_analysis.models.features import FeatureDefinition, FeatureType, PointInTimeNormalizer

    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=3, hidden_dims=(16, 8), output_dim=1)
    model = SmallMLP(cfg)

    # Eğitim verisine fit edilmiş normalizer
    train_features = [
        [1.0, 10.0, 100.0],
        [2.0, 20.0, 200.0],
        [3.0, 30.0, 300.0],
    ]
    normalizer = PointInTimeNormalizer(method="standard")
    normalizer.fit(train_features)

    feat_defs = [
        FeatureDefinition(name="f1", feature_type=FeatureType.LOG_RETURN, window_bars=1),
        FeatureDefinition(name="f2", feature_type=FeatureType.LOG_RETURN, window_bars=5),
        FeatureDefinition(name="f3", feature_type=FeatureType.SMA_RATIO, window_bars=20),
    ]

    _, record = manager.save_artifact(
        model=model,
        model_id="bundled-mlp-v1",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="data-v1.0",
        preprocessor=normalizer,
        feature_definitions=feat_defs,
    )

    assert record.preprocessor_filename == "preprocessor.json"
    assert record.preprocessor_sha256 is not None
    assert record.preprocessor_config is not None
    assert record.feature_definitions is not None
    assert len(record.feature_definitions) == 3

    # Paketlenmiş modeli yükle
    bundled = manager.load_bundled_model("bundled-mlp-v1")
    assert bundled.preprocessor is not None
    assert bundled.preprocessor.is_fitted is True
    assert bundled.preprocessor.means == normalizer.means
    assert len(bundled.feature_definitions) == 3

    # Ham test verisi üzerinde çıkarım
    raw_test = [[2.0, 20.0, 200.0]]
    preds = bundled.predict(raw_test)
    assert preds.shape == (1, 1)

    # Manuel olarak normalize edilmiş veriyle modelin çıktısı birebir örtüşmeli
    model.eval()
    manual_scaled = normalizer.transform(raw_test)
    with torch.no_grad():
        manual_preds = model(torch.tensor(manual_scaled, dtype=torch.float32))
    assert torch.allclose(preds, manual_preds)


def test_bundled_model_tampered_preprocessor_fails(tmp_path: Path) -> None:
    """preprocessor.json dosyası bozulursa SHA-256 ModelIntegrityError fırlatılmalıdır."""
    from stock_analysis.models.features import PointInTimeNormalizer

    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=2, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    normalizer = PointInTimeNormalizer()
    normalizer.fit([[1.0, 2.0], [3.0, 4.0]])

    manager.save_artifact(
        model=model,
        model_id="tampered-prep-model",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="v1",
        preprocessor=normalizer,
    )

    # Preprocessor dosyasını tahrif et
    prep_file = tmp_path / "models" / "tampered-prep-model" / "preprocessor.json"
    with open(prep_file, "a", encoding="utf-8") as f:
        f.write(" ")

    with pytest.raises(ModelIntegrityError, match="Preprocessor SHA-256"):
        manager.load_bundled_model("tampered-prep-model", verify_integrity=True)


def test_bundled_model_feature_dim_mismatch_raises(tmp_path: Path) -> None:
    """Modelin beklediğinden farklı sayıda özellik gelirse hata fırlatılmalıdır."""
    from stock_analysis.models.features import PointInTimeNormalizer

    manager = ModelArtifactManager(base_dir=tmp_path / "models")
    cfg = MLPConfig(input_dim=2, hidden_dims=(8,), output_dim=1)
    model = SmallMLP(cfg)

    normalizer = PointInTimeNormalizer()
    normalizer.fit([[1.0, 2.0], [3.0, 4.0]])

    manager.save_artifact(
        model=model,
        model_id="dim-check-model",
        arch_type=ModelArchitectureType.SMALL_MLP,
        arch_config=cfg,
        seed=42,
        data_version="v1",
        preprocessor=normalizer,
    )

    bundled = manager.load_bundled_model("dim-check-model")

    # 2 yerine 4 özellik gönder
    with pytest.raises(ModelIntegrityError, match="Özellik boyutu uyuşmazlığı"):
        bundled.predict([[1.0, 2.0, 3.0, 4.0]])
