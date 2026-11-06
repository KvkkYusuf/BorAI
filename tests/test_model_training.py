"""PyTorch model eğitim döngüsü: Early stopping, gradient kontrolü ve checkpoint testleri.

FAZ-11 gereksinimlerini doğrular:
- EarlyStopping: Sabır (patience), tolerans (min_delta), ağırlık geri yükleme ve NaN durdurma.
- GradientController: Norm/value clipping, patlayan gradyan (NaN/Inf) tespiti ve hata fırlatma.
- CheckpointManager: Atomik kaydetme, SHA-256 doğrulama, budama (pruning) ve güvenli yükleme.
- train_model: Uçtan uca eğitim döngüsü, metrik geçmişi ve determinizm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from stock_analysis.models.architectures import MLPConfig, SmallMLP
from stock_analysis.models.registry import ModelIntegrityError, ModelSecurityError
from stock_analysis.models.training import (
    CheckpointManager,
    EarlyStopping,
    ExplodingGradientError,
    GradientController,
    TrainingLoopConfig,
    train_model,
)

# ============================================================================
# 1. EarlyStopping Testleri
# ============================================================================


def test_early_stopping_initialization_validation() -> None:
    """Geçersiz parametrelerle başlatıldığında ValueError fırlatılmalıdır."""
    with pytest.raises(ValueError, match="patience >= 1"):
        EarlyStopping(patience=0)

    with pytest.raises(ValueError, match="min_delta >= 0"):
        EarlyStopping(min_delta=-0.1)

    with pytest.raises(ValueError, match="mode 'min' veya 'max'"):
        EarlyStopping(mode="invalid")


def test_early_stopping_min_mode_triggers_after_patience() -> None:
    """Val loss düşmediğinde patience dolunca early stopping devreye girmelidir."""
    es = EarlyStopping(patience=3, min_delta=0.01, mode="min", restore_best_weights=False)

    # Epoch 1: 1.0 (en iyi)
    assert not es.step(1.0, epoch=1)
    assert es.counter == 0
    assert es.best_score == 1.0
    assert es.best_epoch == 1

    # Epoch 2: 0.995 (min_delta 0.01 olduğu için gelişme sayılmaz: 1.0 - 0.01 = 0.99)
    assert not es.step(0.995, epoch=2)
    assert es.counter == 1

    # Epoch 3: 1.05 (arttı)
    assert not es.step(1.05, epoch=3)
    assert es.counter == 2

    # Epoch 4: 1.10 (counter == 3 -> early stop tetiklenmeli)
    assert es.step(1.10, epoch=4)
    assert es.early_stop is True
    assert es.stopped_epoch == 4
    assert es.best_epoch == 1


def test_early_stopping_max_mode() -> None:
    """Doğruluk/Sharpe gibi maksimize edilen metriklerde max modu çalışmalıdır."""
    es = EarlyStopping(patience=2, min_delta=0.05, mode="max")

    assert not es.step(0.70, epoch=1)
    assert es.best_score == 0.70

    # 0.73 -> gelişme yetersiz (< 0.75)
    assert not es.step(0.73, epoch=2)
    assert es.counter == 1

    # 0.80 -> belirgin gelişme, counter sıfırlanmalı
    assert not es.step(0.80, epoch=3)
    assert es.counter == 0
    assert es.best_score == 0.80
    assert es.best_epoch == 3

    # Düşüşler
    assert not es.step(0.78, epoch=4)
    assert es.step(0.77, epoch=5)
    assert es.early_stop is True
    assert es.best_score == 0.80
    assert es.best_epoch == 3


def test_early_stopping_restores_best_weights() -> None:
    """En iyi ağırlıklar bellekte saklanmalı ve restore ile geri yüklenmelidir."""
    model = nn.Linear(1, 1, bias=False)
    es = EarlyStopping(patience=2, mode="min", restore_best_weights=True)

    # Epoch 1: Ağırlık = 1.0, Loss = 0.5 (En iyi)
    with torch.no_grad():
        model.weight.fill_(1.0)
    es.step(0.5, model=model, epoch=1)

    # Epoch 2: Ağırlık = 2.0, Loss = 0.8 (Kötüleşti)
    with torch.no_grad():
        model.weight.fill_(2.0)
    es.step(0.8, model=model, epoch=2)

    # Epoch 3: Ağırlık = 3.0, Loss = 0.9 (Kötüleşti -> Durdu)
    with torch.no_grad():
        model.weight.fill_(3.0)
    es.step(0.9, model=model, epoch=3)

    assert es.early_stop is True
    assert model.weight.item() == pytest.approx(3.0)

    # Restore çağrısı en iyi ağırlığı (1.0) geri yüklemelidir
    es.restore(model)
    assert model.weight.item() == pytest.approx(1.0)


def test_early_stopping_nan_stops_immediately() -> None:
    """NaN kaybı geldiğinde anında durma tetiklenmelidir."""
    es = EarlyStopping(patience=5)
    assert es.step(float("nan"), epoch=1) is True
    assert es.early_stop is True


# ============================================================================
# 2. GradientController Testleri
# ============================================================================


def test_gradient_controller_validation() -> None:
    """Geçersiz parametrelerle başlatıldığında ValueError fırlatılmalıdır."""
    with pytest.raises(ValueError, match="max_norm pozitif"):
        GradientController(max_norm=-1.0)

    with pytest.raises(ValueError, match="clip_value pozitif"):
        GradientController(clip_value=0.0)


def test_gradient_controller_clips_norm() -> None:
    """Gradyan normu max_norm sınırını aştığında kırpılmalıdır."""
    model = nn.Linear(10, 1, bias=False)
    # Gradyan ata: norm = sqrt(10 * 4^2) = sqrt(160) ≈ 12.65
    for p in model.parameters():
        p.grad = torch.full_like(p.data, 4.0)

    controller = GradientController(max_norm=1.0)
    res = controller.inspect_and_clip(model)

    assert res.is_clipped is True
    assert res.total_norm == pytest.approx(12.649, rel=1e-2)
    assert res.clipped_norm == pytest.approx(1.0)
    assert res.has_nan_or_inf is False

    # Parametre gradyanının yeni normunu kontrol et
    new_norm = torch.norm(model.weight.grad).item()
    assert new_norm == pytest.approx(1.0, rel=1e-3)


def test_gradient_controller_exploding_nan_raises() -> None:
    """NaN gradyan oluştuğunda ExplodingGradientError fırlatılmalıdır."""
    model = nn.Linear(5, 1)
    for p in model.parameters():
        p.grad = torch.tensor([float("nan")] * p.numel()).view(p.shape)

    controller = GradientController(raise_on_nan_inf=True)
    with pytest.raises(ExplodingGradientError, match="NaN veya Inf"):
        controller.inspect_and_clip(model)


def test_gradient_controller_no_grad_parameters() -> None:
    """Gradyanı olmayan modelde hata almadan sıfır norm dönmelidir."""
    model = nn.Linear(5, 1)
    controller = GradientController()
    res = controller.inspect_and_clip(model)
    assert res.total_norm == 0.0
    assert res.is_clipped is False


# ============================================================================
# 3. CheckpointManager Testleri
# ============================================================================


def test_checkpoint_manager_atomic_save_and_sha256(tmp_path: Path) -> None:
    """Kontrol noktası kaydedilmeli, SHA-256 özeti oluşturulmalı ve güvenle yüklenmelidir."""
    ckpt_dir = tmp_path / "checkpoints"
    manager = CheckpointManager(base_dir=ckpt_dir, max_to_keep=2)

    model = nn.Linear(4, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

    ckpt_path = manager.save_checkpoint(
        model=model,
        epoch=1,
        metrics={"train_loss": 0.45, "val_loss": 0.50},
        optimizer=optimizer,
    )

    assert ckpt_path.exists()
    assert ckpt_path.with_suffix(".json").exists()

    # Meta verisini doğrula
    with open(ckpt_path.with_suffix(".json"), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["epoch"] == 1
    assert meta["metrics"]["val_loss"] == 0.50
    assert "sha256" in meta

    # Başka bir modele yükle
    new_model = nn.Linear(4, 1)
    new_opt = torch.optim.SGD(new_model.parameters(), lr=0.01)

    info = manager.load_checkpoint(ckpt_path, model=new_model, optimizer=new_opt)
    assert info["epoch"] == 1
    assert info["metrics"]["val_loss"] == 0.50

    # Ağırlıkların birebir eşleştiğini kontrol et
    assert torch.allclose(model.weight, new_model.weight)
    assert torch.allclose(model.bias, new_model.bias)


def test_checkpoint_manager_tampered_fails_integrity(tmp_path: Path) -> None:
    """Dosya bozulursa veya tahrif edilirse SHA-256 ModelIntegrityError fırlatılmalıdır."""
    ckpt_dir = tmp_path / "checkpoints"
    manager = CheckpointManager(base_dir=ckpt_dir)

    model = nn.Linear(3, 1)
    ckpt_path = manager.save_checkpoint(
        model=model,
        epoch=1,
        metrics={"val_loss": 0.20},
    )

    # Dosya içeriğini tahrif et (son baytları değiştir)
    with open(ckpt_path, "ab") as f:
        f.write(b"corrupted_data")

    with pytest.raises(ModelIntegrityError, match="bütünlük doğrulaması başarısız"):
        manager.load_checkpoint(ckpt_path, model=model, verify_sha256=True)


def test_checkpoint_manager_pruning(tmp_path: Path) -> None:
    """max_to_keep sınırını aşan eski kontrol noktaları temizlenmelidir."""
    ckpt_dir = tmp_path / "checkpoints"
    manager = CheckpointManager(base_dir=ckpt_dir, max_to_keep=2)

    model = nn.Linear(2, 1)

    # 4 epoch kaydet
    paths = []
    for ep in range(1, 5):
        p = manager.save_checkpoint(model=model, epoch=ep, metrics={"val_loss": float(10 - ep)})
        paths.append(p)

    # max_to_keep=2 olduğu için ilk 2 checkpoint silinmiş olmalı
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert paths[3].exists()

    # best_model.pt her zaman en iyi modeli saklamalıdır (epoch 4, val_loss 6.0)
    best_path = ckpt_dir / "best_model.pt"
    assert best_path.exists()


def test_checkpoint_manager_path_traversal_protection(tmp_path: Path) -> None:
    """Güvensiz dosya adı path traversal engeline takılmalıdır."""
    manager = CheckpointManager(base_dir=tmp_path)
    model = nn.Linear(2, 1)

    with pytest.raises(ModelSecurityError, match="güvensiz filename"):
        manager.save_checkpoint(model=model, epoch=1, metrics={}, filename="../evil.pt")


# ============================================================================
# 4. train_model Entegrasyon Testleri
# ============================================================================


def test_train_model_end_to_end_convergence(tmp_path: Path) -> None:
    """Sentetik regresyon verisinde train_model başarıyla eğitilmeli ve kayıp düşmelidir."""
    # Veri seti: y = 2*x0 - 3*x1 + 1
    torch.manual_seed(42)
    x = torch.randn(100, 4)
    y = 2.0 * x[:, 0] - 3.0 * x[:, 1] + 1.0 + torch.randn(100) * 0.05

    train_ds = TensorDataset(x[:80], y[:80])
    val_ds = TensorDataset(x[80:], y[80:])

    train_loader = DataLoader(train_ds, batch_size=16, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=16, shuffle=False)

    model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(16, 8), output_dim=1, dropout=0.0))
    criterion = nn.MSELoss()

    config = TrainingLoopConfig(
        epochs=15,
        learning_rate=0.05,
        grad_max_norm=1.0,
        early_stopping_patience=10,
        checkpoint_dir=tmp_path / "train_ckpts",
        seed=42,
    )

    result = train_model(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        criterion=criterion,
        config=config,
    )

    # 15 epoch sonunda eğitim kaybı başlangıçtan belirgin düşük olmalı
    assert len(result.history["train_loss"]) > 0
    assert result.history["train_loss"][-1] < result.history["train_loss"][0]
    assert result.best_metric < 1.0
    assert result.best_checkpoint_path is not None
    assert result.best_checkpoint_path.exists()


def test_train_model_early_stops_on_plateau(tmp_path: Path) -> None:
    """Gelişme olmayan veride early stopping eğitimi erken bitirmelidir."""
    torch.manual_seed(42)
    # Tamamen rastgele gürültü: model genelleştiremez, val loss düşmez
    x_train = torch.randn(50, 4)
    y_train = torch.randn(50)
    x_val = torch.randn(50, 4)
    y_val = torch.randn(50)

    train_loader = DataLoader(TensorDataset(x_train, y_train), batch_size=16)
    val_loader = DataLoader(TensorDataset(x_val, y_val), batch_size=16)

    model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1))
    criterion = nn.MSELoss()

    config = TrainingLoopConfig(
        epochs=30,
        learning_rate=0.001,
        early_stopping_patience=3,
        checkpoint_dir=tmp_path / "ckpts",
        seed=42,
    )

    result = train_model(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        criterion=criterion,
        config=config,
    )

    # 30 epoch yerine çok daha erken durmalıdır (patience=3)
    assert result.stopped_early is True
    assert result.total_epochs < 30
    assert result.stopped_epoch is not None
