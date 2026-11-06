"""PyTorch model artifact kayıt ve tekrarlanabilirlik yöneticisi.

FAZ-11 kapsamında model eğitimi ve deneylerinin izlenebilirliğini sağlar:
- Rastgele seed sabitleme (Python, NumPy, PyTorch, cuDNN determinizmi).
- Model mimari konfigürasyonu, hiperparametreler, özellik setleri ve tahmin hedefleri.
- Zaman serisi veri sürümü (`data_version`, `data_split_id`).
- Değerlendirme metrikleri (loss, MAE, yön doğruluğu, baseline kıyasları).
- Model ağırlıklarının güvenli (weights_only=True) kaydedilmesi ve SHA-256 bütünlük doğrulaması.
- Model kayıt defteri indeksi (`registry_index.json`) ve arama/listeleme.
"""

from __future__ import annotations

import json
import os
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
import torch.nn as nn
from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.models.architectures import (
    CNN1DConfig,
    MLPConfig,
    ModelArchitectureType,
    create_model,
)
from stock_analysis.models.features import FeatureDefinition, PointInTimeNormalizer
from stock_analysis.models.security import (
    ModelIntegrityError,
    ModelRegistryError,
    ModelSecurityError,
    compute_file_sha256,
    safe_load_weights,
    safe_save_weights,
)


class ModelNotFoundError(ModelRegistryError):
    """Model ID kayıt defterinde bulunamadığında fırlatılır."""


def seed_everything(seed: int = 42, deterministic: bool = True) -> int:
    """Tekrarlanabilir deneyler için Python, NumPy ve PyTorch rastgelelik tohumlarını sabitler.

    Args:
        seed: Sabitlenecek rastgelelik tohumu (varsayılan: 42).
        deterministic: True ise cuDNN ve PyTorch deterministik algoritmalarını zorunlu kılar.

    Returns:
        Kullanılan seed değeri.
    """
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        try:
            torch.use_deterministic_algorithms(True, warn_only=True)
        except Exception:
            pass

    return seed


_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_safe_identifier(name: str, param_name: str = "identifier") -> None:
    """Path traversal ve geçersiz dosya adı karakterlerini engeller."""
    if (
        not name
        or not _SAFE_IDENTIFIER_PATTERN.match(name)
        or ".." in name
        or "/" in name
        or "\\" in name
    ):
        raise ModelSecurityError(
            f"Geçersiz veya güvensiz {param_name}: '{name}'. "
            "Yalnızca alfanümerik, tire, alt çizgi ve nokta içerebilir."
        )


class ModelArtifactRecord(BaseModel):
    """Bir model artifact'ının değişmez meta veri kaydı."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    arch_type: ModelArchitectureType
    arch_config: dict[str, Any]
    seed: int
    data_version: str
    data_split_id: str | None = None
    feature_names: list[str] = Field(default_factory=list)
    target_type: str = "percentage_return"
    target_horizon: str = "1d"
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    weights_filename: str = "weights.pt"
    weights_sha256: str
    weights_format: str = "pytorch"
    trained_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    preprocessor_filename: str | None = None
    preprocessor_sha256: str | None = None
    preprocessor_config: dict[str, Any] | None = None
    feature_definitions: list[dict[str, Any]] | None = None


@dataclass
class BundledModel:
    """Model ağırlıkları, normalizasyon ve özellik tanımlarını bir arada tutan paket."""

    model: nn.Module
    record: ModelArtifactRecord
    preprocessor: PointInTimeNormalizer | None = None
    feature_definitions: tuple[FeatureDefinition, ...] = ()

    def predict(
        self,
        raw_features: Sequence[Sequence[float]] | torch.Tensor,
        device: torch.device | str = "cpu",
    ) -> torch.Tensor:
        """Ham özellik matrisini eğitimde öğrenilen normalizasyonla dönüştürüp tahmin üretir."""
        self.model.eval()
        selected_device = torch.device(device)
        self.model.to(selected_device)

        if isinstance(raw_features, torch.Tensor):
            if self.preprocessor is not None:
                matrix = raw_features.cpu().numpy().tolist()
                scaled_matrix = self.preprocessor.transform(matrix)
                input_tensor = torch.tensor(
                    scaled_matrix, dtype=torch.float32, device=selected_device
                )
            else:
                input_tensor = raw_features.to(selected_device)
        else:
            if not raw_features or not raw_features[0]:
                raise ValueError("Boş özellik matrisi verilemez.")

            expected_dim = self.record.arch_config.get("input_dim")
            actual_dim = len(raw_features[0])
            if expected_dim is not None and actual_dim != expected_dim:
                raise ModelIntegrityError(
                    f"Özellik boyutu uyuşmazlığı! Modelin beklediği: {expected_dim}, "
                    f"Gelen özellik boyutu: {actual_dim}."
                )

            if self.preprocessor is not None:
                scaled_matrix = self.preprocessor.transform(raw_features)
                input_tensor = torch.tensor(
                    scaled_matrix, dtype=torch.float32, device=selected_device
                )
            else:
                input_tensor = torch.tensor(
                    raw_features, dtype=torch.float32, device=selected_device
                )

        with torch.no_grad():
            preds = self.model(input_tensor)
        assert isinstance(preds, torch.Tensor)
        return preds


class ModelArtifactManager:
    """Model ağırlıklarını ve meta verilerini yöneten güvenli kayıt defteri."""

    def __init__(self, base_dir: Path | str = "ml/model_registry") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "registry_index.json"
        self._ensure_index()

    def _ensure_index(self) -> None:
        """Kayıt indeksi dosyasını oluşturur veya mevcut olanı doğrular."""
        if not self.index_path.exists():
            self._write_index([])

    def _read_index(self) -> list[dict[str, Any]]:
        """Kayıt indeksi dosyasını okur."""
        if not self.index_path.exists():
            return []
        try:
            with self.index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _write_index(self, records: list[dict[str, Any]]) -> None:
        """Kayıt indeksi dosyasını atomik olarak yazar."""
        temp_path = self.index_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)
        temp_path.replace(self.index_path)

    def save_artifact(
        self,
        model: nn.Module,
        model_id: str,
        arch_type: ModelArchitectureType | str,
        arch_config: MLPConfig | CNN1DConfig | dict[str, Any],
        seed: int,
        data_version: str,
        weights_filename: str = "weights.pt",
        weights_format: Literal["safetensors", "pytorch"] | None = None,
        data_split_id: str | None = None,
        feature_names: list[str] | None = None,
        target_type: str = "percentage_return",
        target_horizon: str = "1d",
        hyperparameters: dict[str, Any] | None = None,
        metrics: dict[str, float] | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
        preprocessor: PointInTimeNormalizer | dict[str, Any] | None = None,
        preprocessor_filename: str = "preprocessor.json",
        feature_definitions: Sequence[FeatureDefinition] | list[dict[str, Any]] | None = None,
    ) -> tuple[Path, ModelArtifactRecord]:
        """Model ağırlıklarını, normalizasyonu ve tüm eğitim meta verilerini kaydeder.

        Args:
            model: PyTorch model nesnesi.
            model_id: Benzersiz model tanımlayıcısı.
            arch_type: Model mimari türü (small_mlp, small_cnn_1d vb.).
            arch_config: Mimari yapılandırma nesnesi veya sözlüğü.
            seed: Eğitimde kullanılan rastgelelik tohumu.
            data_version: Veri seti sürümü veya içeriğin SHA-256 özeti.
            weights_filename: Ağırlık dosyasının adı (varsayılan: weights.pt).
            weights_format: Kayıt formatı ("safetensors" veya "pytorch").
                Belirtilmezse dosya uzantısından otomatik tespit edilir.
            data_split_id: Kullanılan kronolojik split ID'si.
            feature_names: Modelin kullandığı öznitelik isimleri listesi.
            target_type: Hedef türü (ör. getiri, yön sınıflandırması).
            target_horizon: Tahmin ufku (ör. 1d, 5d, 20d).
            hyperparameters: Öğrenme oranı, batch boyutu vb. hiperparametreler.
            metrics: Eğitim ve doğrulama metrikleri (loss, MAE, doğruluk vb.).
            tags: Etiketler listesi.
            notes: Ek açıklama notları.
            preprocessor: Eğitimde fit edilen PointInTimeNormalizer veya yapılandırma.
            preprocessor_filename: Normalizer dosya adı (varsayılan: preprocessor.json).
            feature_definitions: Modelin girdi özelliklerinin nedensel tanımları.

        Returns:
            (artifact_klasor_yolu, ModelArtifactRecord)
        """
        _validate_safe_identifier(model_id, "model_id")

        # Format ve dosya adı tespiti
        if weights_format is None:
            if weights_filename.endswith(".safetensors"):
                effective_format: Literal["safetensors", "pytorch"] = "safetensors"
            else:
                effective_format = "pytorch"
        else:
            effective_format = weights_format

        if effective_format == "safetensors" and not weights_filename.endswith(".safetensors"):
            if weights_filename == "weights.pt":
                weights_filename = "weights.safetensors"
            else:
                weights_filename = f"{Path(weights_filename).stem}.safetensors"

        _validate_safe_identifier(weights_filename, "weights_filename")

        model_dir = self.base_dir / model_id
        model_dir.mkdir(parents=True, exist_ok=True)

        # Model state_dict'ini güvenli formatta kaydet ve SHA-256 hesapla
        weights_path, weights_sha256 = safe_save_weights(
            model_or_state_dict=model,
            file_path=model_dir / weights_filename,
            weights_format=effective_format,
            metadata={"model_id": model_id, "data_version": data_version},
        )
        saved_weights_filename = weights_path.name

        # Preprocessor serileştirme ve SHA-256 hash
        preprocessor_sha256: str | None = None
        preprocessor_dict: dict[str, Any] | None = None
        saved_preprocessor_filename: str | None = None

        if preprocessor is not None:
            _validate_safe_identifier(preprocessor_filename, "preprocessor_filename")
            if hasattr(preprocessor, "to_dict"):
                preprocessor_dict = preprocessor.to_dict()
            elif isinstance(preprocessor, dict):
                preprocessor_dict = dict(preprocessor)
            else:
                raise TypeError("preprocessor PointInTimeNormalizer veya dict türünde olmalıdır.")

            preprocessor_path = model_dir / preprocessor_filename
            with preprocessor_path.open("w", encoding="utf-8") as f:
                json.dump(preprocessor_dict, f, indent=2)

            preprocessor_sha256 = compute_file_sha256(preprocessor_path)
            saved_preprocessor_filename = preprocessor_filename

        serialized_feature_defs: list[dict[str, Any]] | None = None
        if feature_definitions is not None:
            serialized_feature_defs = [
                f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in feature_definitions
            ]

        normalized_arch_type = (
            ModelArchitectureType(arch_type) if isinstance(arch_type, str) else arch_type
        )

        config_dict: dict[str, Any]
        if hasattr(arch_config, "__dict__"):
            config_dict = {k: v for k, v in arch_config.__dict__.items() if not k.startswith("_")}
        elif isinstance(arch_config, dict):
            config_dict = dict(arch_config)
        else:
            raise TypeError("arch_config bir dataclass veya dict olmalıdır.")

        record = ModelArtifactRecord(
            model_id=model_id,
            arch_type=normalized_arch_type,
            arch_config=config_dict,
            seed=seed,
            data_version=data_version,
            data_split_id=data_split_id,
            feature_names=feature_names or [],
            target_type=target_type,
            target_horizon=target_horizon,
            hyperparameters=hyperparameters or {},
            metrics=metrics or {},
            weights_filename=saved_weights_filename,
            weights_sha256=weights_sha256,
            weights_format=effective_format,
            trained_at=datetime.now(UTC),
            tags=tags or [],
            notes=notes,
            preprocessor_filename=saved_preprocessor_filename,
            preprocessor_sha256=preprocessor_sha256,
            preprocessor_config=preprocessor_dict,
            feature_definitions=serialized_feature_defs,
        )

        # metadata.json dosyasını kaydet
        meta_path = model_dir / "metadata.json"
        with meta_path.open("w", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))

        # İndekse ekle veya güncelle
        self._update_index(record)

        return model_dir, record

    def _update_index(self, record: ModelArtifactRecord) -> None:
        """Kayıt indeksi listesini günceller."""
        records = self._read_index()
        # Varsa eskiyi kaldır
        records = [r for r in records if r.get("model_id") != record.model_id]
        records.append(json.loads(record.model_dump_json()))
        self._write_index(records)

    def get_metadata(self, model_id: str) -> ModelArtifactRecord:
        """Belirtilen model_id'ye ait meta veri kaydını getirir."""
        _validate_safe_identifier(model_id, "model_id")
        meta_path = self.base_dir / model_id / "metadata.json"
        if not meta_path.exists():
            raise ModelNotFoundError(f"Model kaydı bulunamadı: {model_id}")

        with meta_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return ModelArtifactRecord(**data)

    def load_weights_into_model(
        self,
        model: nn.Module,
        model_id: str,
        verify_integrity: bool = True,
        map_location: str = "cpu",
    ) -> ModelArtifactRecord:
        """Mevcut bir PyTorch model nesnesine kayıtlı ağırlıkları güvenle yükler."""
        _validate_safe_identifier(model_id, "model_id")
        record = self.get_metadata(model_id)

        model_dir = self.base_dir / model_id
        weights_path = model_dir / record.weights_filename

        if not weights_path.exists():
            raise ModelNotFoundError(f"Ağırlık dosyası bulunamadı: {weights_path}")

        # Güvenlik denetimleri, SHA-256 doğrulaması ve weights_only/safetensors garantili yükleme
        state_dict = safe_load_weights(
            file_path=weights_path,
            expected_sha256=record.weights_sha256 if verify_integrity else None,
            verify_integrity=verify_integrity,
            map_location=map_location,
        )
        model.load_state_dict(state_dict, strict=True)
        model.eval()
        return record

    def load_artifact(
        self,
        model_id: str,
        verify_integrity: bool = True,
        map_location: str = "cpu",
    ) -> tuple[nn.Module, ModelArtifactRecord]:
        """Modeli mimarisini yeniden oluşturup ağırlıkları güvenle yükleyerek döner."""
        record = self.get_metadata(model_id)
        model = create_model(record.arch_type, record.arch_config)
        self.load_weights_into_model(
            model=model,
            model_id=model_id,
            verify_integrity=verify_integrity,
            map_location=map_location,
        )
        return model, record

    def load_preprocessor(
        self,
        model_id: str,
        verify_integrity: bool = True,
    ) -> PointInTimeNormalizer | None:
        """Kayıtlı modele ait fit edilmiş PointInTimeNormalizer nesnesini yükler."""
        record = self.get_metadata(model_id)
        if not record.preprocessor_filename:
            return None

        _validate_safe_identifier(record.preprocessor_filename, "preprocessor_filename")
        preprocessor_path = self.base_dir / model_id / record.preprocessor_filename
        if not preprocessor_path.exists():
            raise ModelNotFoundError(
                f"Normalizer/preprocessor dosyası bulunamadı: {preprocessor_path}"
            )

        if verify_integrity and record.preprocessor_sha256:
            actual_sha256 = compute_file_sha256(preprocessor_path)
            if actual_sha256 != record.preprocessor_sha256:
                raise ModelIntegrityError(
                    f"Preprocessor SHA-256 bütünlük doğrulaması başarısız! "
                    f"Beklenen: {record.preprocessor_sha256}, Gerçekleşen: {actual_sha256}."
                )

        with preprocessor_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return PointInTimeNormalizer.from_dict(data)

    def load_bundled_model(
        self,
        model_id: str,
        verify_integrity: bool = True,
        map_location: str = "cpu",
    ) -> BundledModel:
        """Modeli, normalizasyon parametrelerini ve özellik tanımlarını paket halinde döner."""
        model, record = self.load_artifact(
            model_id=model_id,
            verify_integrity=verify_integrity,
            map_location=map_location,
        )
        preprocessor = self.load_preprocessor(
            model_id=model_id,
            verify_integrity=verify_integrity,
        )
        feature_defs: tuple[FeatureDefinition, ...] = ()
        if record.feature_definitions:
            feature_defs = tuple(FeatureDefinition.from_dict(d) for d in record.feature_definitions)

        return BundledModel(
            model=model,
            record=record,
            preprocessor=preprocessor,
            feature_definitions=feature_defs,
        )

    def list_records(self) -> list[ModelArtifactRecord]:
        """Kayıt defterindeki tüm modelleri listeler."""
        raw_list = self._read_index()
        results: list[ModelArtifactRecord] = []
        for item in raw_list:
            try:
                results.append(ModelArtifactRecord(**item))
            except Exception:
                continue
        # En yeni tarih en başta olacak şekilde sırala
        results.sort(key=lambda r: r.trained_at, reverse=True)
        return results

    def get_best_model(
        self,
        metric_name: str,
        higher_is_better: bool = False,
        target_type: str | None = None,
        target_horizon: str | None = None,
    ) -> ModelArtifactRecord | None:
        """Belirtilen metriğe göre en başarılı modeli bulur."""
        candidates = self.list_records()
        if target_type:
            candidates = [c for c in candidates if c.target_type == target_type]
        if target_horizon:
            candidates = [c for c in candidates if c.target_horizon == target_horizon]

        filtered = [c for c in candidates if metric_name in c.metrics]
        if not filtered:
            return None

        return sorted(
            filtered,
            key=lambda c: c.metrics[metric_name],
            reverse=higher_is_better,
        )[0]

    def delete_artifact(self, model_id: str) -> bool:
        """Bir model artifact'ını ve klasörünü siler."""
        _validate_safe_identifier(model_id, "model_id")
        model_dir = self.base_dir / model_id
        if not model_dir.exists():
            return False

        for f in model_dir.iterdir():
            if f.is_file():
                f.unlink()
        model_dir.rmdir()

        records = self._read_index()
        records = [r for r in records if r.get("model_id") != model_id]
        self._write_index(records)
        return True


__all__ = [
    "BundledModel",
    "ModelArtifactManager",
    "ModelArtifactRecord",
    "ModelIntegrityError",
    "ModelNotFoundError",
    "ModelRegistryError",
    "ModelSecurityError",
    "compute_file_sha256",
    "safe_load_weights",
    "safe_save_weights",
    "seed_everything",
]
