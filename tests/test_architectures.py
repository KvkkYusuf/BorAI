"""PyTorch başlangıç mimarileri (SmallMLP, SmallCNN1D) birim testleri."""

import pytest
import torch

from stock_analysis.models.architectures import (
    CNN1DConfig,
    MLPConfig,
    ModelArchitectureType,
    SmallCNN1D,
    SmallMLP,
    count_parameters,
    create_model,
    evaluate_architecture_justification,
)


def test_mlp_config_validation() -> None:
    """MLPConfig parametre sınırlarını doğrular."""
    # Geçerli konfigürasyon
    cfg = MLPConfig(input_dim=10, hidden_dims=(32, 16), output_dim=1, dropout=0.2)
    assert cfg.input_dim == 10
    assert cfg.hidden_dims == (32, 16)

    # Geçersiz girdi boyutu
    with pytest.raises(ValueError, match="input_dim pozitif"):
        MLPConfig(input_dim=0)

    # Geçersiz dropout
    with pytest.raises(ValueError, match="dropout"):
        MLPConfig(input_dim=10, dropout=1.5)

    # Boş gizli katman listesi
    with pytest.raises(ValueError, match="En az bir gizli katman"):
        MLPConfig(input_dim=10, hidden_dims=())


def test_cnn1d_config_validation() -> None:
    """CNN1DConfig parametre sınırlarını doğrular."""
    cfg = CNN1DConfig(in_channels=5, seq_len=20, num_filters=(16, 32), kernel_size=3)
    assert cfg.in_channels == 5
    assert cfg.seq_len == 20

    with pytest.raises(ValueError, match="in_channels pozitif"):
        CNN1DConfig(in_channels=0, seq_len=10)

    with pytest.raises(ValueError, match="seq_len pozitif"):
        CNN1DConfig(in_channels=5, seq_len=-1)

    with pytest.raises(ValueError, match="kernel_size pozitif"):
        CNN1DConfig(in_channels=5, seq_len=10, kernel_size=0)


def test_small_mlp_forward_and_backward() -> None:
    """SmallMLP ileri ve geri geçişlerini test eder."""
    torch.manual_seed(42)
    cfg = MLPConfig(input_dim=8, hidden_dims=(32, 16), output_dim=1, dropout=0.1)
    model = SmallMLP(cfg)

    # Parametre sayısı kontrolü (küçük ve CPU dostu olmalı)
    param_count = count_parameters(model)
    assert 0 < param_count < 10000

    # 2D girdi: (batch_size=4, input_dim=8)
    x = torch.randn(4, 8)
    out = model(x)
    assert out.shape == (4, 1)

    # Geri geçiş (backward)
    loss = out.sum()
    loss.backward()

    for p in model.parameters():
        assert p.grad is not None

    # Hatalı boyut denetimi
    with pytest.raises(ValueError, match="Girdi boyutu"):
        model(torch.randn(4, 7))


def test_small_mlp_flattening_multidimensional_input() -> None:
    """SmallMLP'nin 3D zamansal girdiyi otomatik düzleştirdiğini test eder."""
    cfg = MLPConfig(input_dim=24, hidden_dims=(32,), output_dim=1)
    model = SmallMLP(cfg)

    # (batch=2, seq_len=6, features=4) -> 6 * 4 = 24
    x_3d = torch.randn(2, 6, 4)
    out = model(x_3d)
    assert out.shape == (2, 1)


def test_small_mlp_eval_mode_determinism() -> None:
    """Eval modunda dropout pasifleştirilerek deterministik çıktı üretilmeli."""
    cfg = MLPConfig(input_dim=10, hidden_dims=(32,), output_dim=1, dropout=0.5)
    model = SmallMLP(cfg)
    model.eval()

    x = torch.randn(5, 10)
    out1 = model(x)
    out2 = model(x)
    assert torch.allclose(out1, out2)


def test_small_cnn1d_forward_and_backward() -> None:
    """SmallCNN1D modelinin ileri ve geri geçişlerini test eder."""
    torch.manual_seed(42)
    cfg = CNN1DConfig(
        in_channels=4,
        seq_len=15,
        num_filters=(16, 32),
        kernel_size=3,
        output_dim=1,
        dropout=0.1,
    )
    model = SmallCNN1D(cfg)

    param_count = count_parameters(model)
    assert 0 < param_count < 15000

    # Standart format: (batch_size=3, in_channels=4, seq_len=15)
    x_std = torch.randn(3, 4, 15)
    out = model(x_std)
    assert out.shape == (3, 1)

    loss = out.mean()
    loss.backward()

    for p in model.parameters():
        assert p.grad is not None

    # Zaman-özellik biçimi: (batch_size=3, seq_len=15, in_channels=4) - otomatik transpozisyon
    x_transposed = torch.randn(3, 15, 4)
    model.eval()
    out_transposed = model(x_transposed)
    assert out_transposed.shape == (3, 1)


def test_small_cnn1d_dimension_mismatch() -> None:
    """SmallCNN1D uyumsuz boyutlarda hata fırlatmalıdır."""
    cfg = CNN1DConfig(in_channels=4, seq_len=10, num_filters=(8,))
    model = SmallCNN1D(cfg)

    # Yanlış kanal sayısı
    with pytest.raises(ValueError, match="Girdi şekli"):
        model(torch.randn(2, 6, 10))

    # 2D girdi (3D bekleniyor)
    with pytest.raises(ValueError, match="3 boyutlu olmalıdır"):
        model(torch.randn(2, 4))


def test_architecture_justification_baseline_not_beaten() -> None:
    """Baseline geçilemediğinde karmaşık mimarilere izin verilmez."""
    justification = evaluate_architecture_justification(
        sample_count=100000,
        sequence_length=50,
        feature_count=10,
        baseline_beaten=False,
    )
    assert justification.recommended_type == ModelArchitectureType.SMALL_MLP
    assert not justification.is_complex_model_justified
    assert any("baseline" in r.lower() for r in justification.reasons)


def test_architecture_justification_short_sequence() -> None:
    """Kısa zamansal pencere için 1D CNN önerilmeli."""
    justification = evaluate_architecture_justification(
        sample_count=5000,
        sequence_length=15,
        feature_count=6,
        baseline_beaten=True,
    )
    assert justification.recommended_type == ModelArchitectureType.SMALL_CNN_1D
    assert not justification.is_complex_model_justified


def test_architecture_justification_tabular_features() -> None:
    """Tekil veya özet özellikler için SmallMLP önerilmeli."""
    justification = evaluate_architecture_justification(
        sample_count=5000,
        sequence_length=1,
        feature_count=20,
        baseline_beaten=True,
    )
    assert justification.recommended_type == ModelArchitectureType.SMALL_MLP
    assert not justification.is_complex_model_justified


def test_architecture_justification_large_data_long_seq() -> None:
    """Yeterli veri ve uzun zaman dizisi varsa LSTM gerekçelendirilebilir."""
    justification = evaluate_architecture_justification(
        sample_count=60000,
        sequence_length=40,
        feature_count=8,
        baseline_beaten=True,
    )
    assert justification.recommended_type == ModelArchitectureType.LSTM
    assert justification.is_complex_model_justified


def test_create_model_factory() -> None:
    """create_model fabrika fonksiyonu nesneleri doğru oluşturmalıdır."""
    # SmallMLP dataclass ile
    mlp = create_model(
        ModelArchitectureType.SMALL_MLP,
        MLPConfig(input_dim=5, hidden_dims=(16,), output_dim=1),
    )
    assert isinstance(mlp, SmallMLP)

    # SmallCNN1D dict ile
    cnn = create_model(
        "small_cnn_1d",
        {"in_channels": 3, "seq_len": 10, "num_filters": (16,), "output_dim": 1},
    )
    assert isinstance(cnn, SmallCNN1D)

    # Geçersiz tip
    with pytest.raises(TypeError):
        create_model(ModelArchitectureType.SMALL_MLP, "invalid_config")  # type: ignore[arg-type]

    # Henüz izin verilmeyen / gerekçelendirilmemiş karmaşık model
    with pytest.raises(NotImplementedError, match="veri ve deney gerekçelendirmesi"):
        create_model(ModelArchitectureType.TRANSFORMER, {})
