"""Model yükleme güvenliği, hash bütünlüğü ve safetensors testleri.

FAZ-11 v2 gereksinimlerini doğrular:
- safetensors desteği (sıfır bağımlılıklı veya kütüphaneli, saf unpickled format).
- weights_only=True zorunluluğu ve zararlı pickle opcode tespiti.
- SHA-256 hash doğrulama ve bütünlük hatası denetimleri.
- Yasaklı uzantılar (.pkl, .pickle, .exe vb.) ve path traversal engellemeleri.
- Checkpoint ve ModelRegistry uçtan uca güvenli yükleme akışları.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pytest
import torch

from stock_analysis.models.architectures import MLPConfig, SmallMLP
from stock_analysis.models.registry import (
    ModelArtifactManager,
    ModelIntegrityError,
    ModelSecurityError,
    compute_file_sha256,
)
from stock_analysis.models.security import (
    inspect_weights_file_security,
    load_safetensors,
    safe_load_weights,
    safe_save_weights,
    save_safetensors,
    validate_safe_filename,
    verify_file_sha256,
)
from stock_analysis.models.training import CheckpointManager


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_safetensors_save_and_load_roundtrip(tmp_dir: Path) -> None:
    """Safetensors formatının tensör tipleri ve değerlerini kayıpsız koruduğunu doğrular."""
    st_path = tmp_dir / "weights.safetensors"
    tensors = {
        "layer1.weight": torch.randn(4, 8, dtype=torch.float32),
        "layer1.bias": torch.randn(4, dtype=torch.float32),
        "precision_double": torch.randn(2, 2, dtype=torch.float64),
        "indices": torch.tensor([1, 5, 9, 12], dtype=torch.int64),
        "mask": torch.tensor([True, False, True], dtype=torch.bool),
    }

    metadata = {"model_name": "test_model", "author": "borai"}
    sha256_hash = save_safetensors(tensors, st_path, metadata=metadata)

    assert st_path.is_file()
    assert len(sha256_hash) == 64
    assert compute_file_sha256(st_path) == sha256_hash

    # Geri yükle ve tensör eşitliklerini kontrol et
    loaded = load_safetensors(st_path, device="cpu")
    assert set(loaded.keys()) == set(tensors.keys())

    assert torch.allclose(loaded["layer1.weight"], tensors["layer1.weight"])
    assert torch.allclose(loaded["layer1.bias"], tensors["layer1.bias"])
    assert torch.allclose(loaded["precision_double"], tensors["precision_double"])
    assert torch.equal(loaded["indices"], tensors["indices"])
    assert torch.equal(loaded["mask"], tensors["mask"])


def test_safe_save_and_load_safetensors(tmp_dir: Path) -> None:
    """safe_save_weights ve safe_load_weights safetensors entegrasyonunu doğrular."""
    model = SmallMLP(MLPConfig(input_dim=5, hidden_dims=(16,), output_dim=1))
    weights_path = tmp_dir / "model.safetensors"

    saved_path, expected_hash = safe_save_weights(model, weights_path, weights_format="safetensors")
    assert saved_path.suffix == ".safetensors"

    # Doğru hash ile yükleme
    state_dict = safe_load_weights(
        saved_path,
        expected_sha256=expected_hash,
        verify_integrity=True,
    )
    assert "net.0.weight" in state_dict or "layers.0.weight" in state_dict or len(state_dict) > 0


def test_sha256_integrity_verification_failure(tmp_dir: Path) -> None:
    """Dosya içeriği tahrif edildiğinde ModelIntegrityError fırlatıldığını doğrular."""
    file_path = tmp_dir / "valid.safetensors"
    tensors = {"w": torch.tensor([1.0, 2.0, 3.0])}
    original_hash = save_safetensors(tensors, file_path)

    # Sağlam dosyada hash doğrulaması başarılı olmalı
    verify_file_sha256(file_path, original_hash)

    # Dosyayı tahrif et (1 bayt değiştir)
    with file_path.open("r+b") as f:
        f.seek(-1, 2)
        f.write(b"\x00")

    # Hash doğrulaması artık başarısız olmalı
    with pytest.raises(ModelIntegrityError, match=r"SHA-256"):
        verify_file_sha256(file_path, original_hash)

    with pytest.raises(ModelIntegrityError, match=r"SHA-256"):
        safe_load_weights(file_path, expected_sha256=original_hash, verify_integrity=True)


def test_forbidden_file_extensions_rejected(tmp_dir: Path) -> None:
    """Yasaklı uzantıların (.pkl, .pickle, .exe) doğrudan reddedildiğini doğrular."""
    for ext in (".pkl", ".pickle", ".exe", ".sh"):
        bad_path = tmp_dir / f"weights{ext}"
        bad_path.write_text("malicious content")

        with pytest.raises(ModelSecurityError, match="yasaklanmıştır"):
            inspect_weights_file_security(bad_path)

        with pytest.raises(ModelSecurityError, match="yasaklanmıştır"):
            safe_load_weights(bad_path)


def test_unsupported_extensions_rejected(tmp_dir: Path) -> None:
    """Desteklenmeyen bilinmeyen uzantıların reddedildiğini doğrular."""
    unknown_path = tmp_dir / "weights.xyz"
    unknown_path.write_text("dummy")

    with pytest.raises(ModelSecurityError, match="Desteklenmeyen model ağırlık uzantısı"):
        inspect_weights_file_security(unknown_path)


def test_malicious_pickle_payload_blocked(tmp_dir: Path) -> None:
    """İçinde yetkisiz kod yürütme opcode'u barındıran pickle dosyasının engellendiğini doğrular."""

    class Exploit:
        def __reduce__(self) -> tuple[Any, ...]:
            return (eval, ("1+1",))

    bad_pt = tmp_dir / "exploit.pt"
    with bad_pt.open("wb") as f:
        pickle.dump(Exploit(), f)

    with pytest.raises(ModelSecurityError):
        safe_load_weights(bad_pt)


def test_path_traversal_validation() -> None:
    """Path traversal ve geçersiz karakter içeren tanımlayıcıların engellendiğini doğrular."""
    invalid_names = [
        "../secret",
        "../../etc/passwd",
        "model/sub",
        "model\\sub",
        "",
        "model;rm -rf",
    ]
    for bad_name in invalid_names:
        with pytest.raises(ModelSecurityError):
            validate_safe_filename(bad_name)


def test_model_registry_safetensors_artifact_lifecycle(tmp_dir: Path) -> None:
    """ModelRegistry ile safetensors formatında artifact kaydetme ve yükleme döngüsünü doğrular."""
    manager = ModelArtifactManager(base_dir=tmp_dir / "registry")
    model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1))

    # Safetensors formatında kaydet
    _, record = manager.save_artifact(
        model=model,
        model_id="safe_mlp_v1",
        arch_type="small_mlp",
        arch_config=MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1),
        seed=42,
        data_version="v2.0",
        weights_format="safetensors",
    )

    assert record.weights_format == "safetensors"
    assert record.weights_filename.endswith(".safetensors")
    assert len(record.weights_sha256) == 64

    # Model weights'lerini yeni bir modele güvenle yükle
    new_model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1))
    loaded_record = manager.load_weights_into_model(
        model=new_model,
        model_id="safe_mlp_v1",
        verify_integrity=True,
    )
    assert loaded_record.model_id == "safe_mlp_v1"

    # Ağırlıkların tam olarak aynı olduğunu doğrula
    orig_params = list(model.parameters())
    loaded_params = list(new_model.parameters())
    for p1, p2 in zip(orig_params, loaded_params, strict=True):
        assert torch.allclose(p1, p2)


def test_model_registry_detects_tampered_weights(tmp_dir: Path) -> None:
    """ModelRegistry'nin tahrif edilmiş dosyada ModelIntegrityError fırlattığını doğrular."""
    manager = ModelArtifactManager(base_dir=tmp_dir / "registry")
    model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1))

    _, record = manager.save_artifact(
        model=model,
        model_id="tamper_target",
        arch_type="small_mlp",
        arch_config=MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1),
        seed=42,
        data_version="v1",
        weights_format="safetensors",
    )

    weights_file = tmp_dir / "registry" / "tamper_target" / record.weights_filename
    # Dosyanın son baytını değiştir
    with weights_file.open("r+b") as f:
        f.seek(-1, 2)
        f.write(b"\xff")

    new_model = SmallMLP(MLPConfig(input_dim=4, hidden_dims=(8,), output_dim=1))
    with pytest.raises(ModelIntegrityError, match=r"SHA-256"):
        manager.load_weights_into_model(
            model=new_model,
            model_id="tamper_target",
            verify_integrity=True,
        )


def test_checkpoint_manager_weights_only_and_hash_integrity(tmp_dir: Path) -> None:
    """CheckpointManager'ın weights_only=True ve SHA-256 bütünlüğünü doğrular."""
    ckpt_dir = tmp_dir / "checkpoints"
    manager = CheckpointManager(base_dir=ckpt_dir, max_to_keep=3)
    model = SmallMLP(MLPConfig(input_dim=3, hidden_dims=(6,), output_dim=1))

    ckpt_path = manager.save_checkpoint(
        model=model,
        epoch=1,
        metrics={"val_loss": 0.42},
    )

    # Güvenli yükleme
    new_model = SmallMLP(MLPConfig(input_dim=3, hidden_dims=(6,), output_dim=1))
    ckpt_info = manager.load_checkpoint(
        checkpoint_path=ckpt_path,
        model=new_model,
        verify_sha256=True,
    )
    assert ckpt_info["epoch"] == 1
    assert ckpt_info["metrics"]["val_loss"] == 0.42

    # Checkpoint meta dosyasındaki hash uyuşmazlığında hata
    meta_path = ckpt_path.with_suffix(".json")
    with meta_path.open("r", encoding="utf-8") as f:
        meta_data = json.load(f)
    meta_data["sha256"] = "0" * 64
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta_data, f)

    with pytest.raises(
        ModelIntegrityError, match="Checkpoint SHA-256 bütünlük doğrulaması başarısız"
    ):
        manager.load_checkpoint(
            checkpoint_path=ckpt_path,
            model=new_model,
            verify_sha256=True,
        )


def test_file_size_dos_protection(tmp_dir: Path) -> None:
    """Aşırı büyük dosyaların DoS önleme kapsamında reddedildiğini doğrular."""
    file_path = tmp_dir / "large_model.safetensors"
    # 10 baytlık dosya oluştur ancak maksimum sınırı 5 bayt ver
    file_path.write_bytes(b"0123456789")

    with pytest.raises(ModelSecurityError, match="izin verilen sınırı aşıyor"):
        inspect_weights_file_security(file_path, max_file_size_bytes=5)
