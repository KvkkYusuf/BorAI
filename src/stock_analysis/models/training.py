"""PyTorch model eğitim döngüsü: Early stopping, gradient kontrolü ve checkpoint yönetimi.

FAZ-11 kapsamında güvenli ve tekrarlanabilir model eğitimini sağlar:
- EarlyStopping: Aşırı öğrenmeyi engelleyen, sabır (patience) kontrollü durdurucu.
- GradientController: Gradyan patlaması ve NaN/Inf tespiti ile norm/value clipping.
- CheckpointManager: Durumları SHA-256 kontrolüyle kaydeden ve budayan yönetici.
- train_model: Tüm bu mekanizmaları bir araya getiren deterministik eğitim döngüsü.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from stock_analysis.models.registry import (
    ModelIntegrityError,
    ModelSecurityError,
    compute_file_sha256,
    seed_everything,
)

_SAFE_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_safe_name(name: str, param_name: str = "filename") -> None:
    """Path traversal ve geçersiz dosya adı karakterlerini engeller."""
    if (
        not name
        or not _SAFE_FILENAME_PATTERN.match(name)
        or ".." in name
        or "/" in name
        or "\\" in name
    ):
        raise ModelSecurityError(
            f"Geçersiz veya güvensiz {param_name}: '{name}'. "
            "Yalnızca alfanümerik, tire, alt çizgi ve nokta içerebilir."
        )


class ExplodingGradientError(RuntimeError):
    """Gradyanlarda NaN veya sonsuz (Inf) değer tespit edildiğinde fırlatılır."""


@dataclass
class GradientCheckResult:
    """Gradyan denetim ve kırpma sonucu."""

    total_norm: float
    is_clipped: bool
    has_nan_or_inf: bool
    clipped_norm: float | None = None
    message: str = "OK"


class GradientController:
    """Eğitim sırasında gradyan kararlılığını ve sınırlarını denetleyen kontrolör.

    Özellikler:
    - NaN ve Inf gradyan tespiti.
    - L2/p-norm gradyan kırpma (torch.nn.utils.clip_grad_norm_).
    - Değer bazlı gradyan kırpma (torch.nn.utils.clip_grad_value_).
    """

    def __init__(
        self,
        max_norm: float | None = 1.0,
        norm_type: float = 2.0,
        clip_value: float | None = None,
        raise_on_nan_inf: bool = True,
    ) -> None:
        if max_norm is not None and max_norm <= 0:
            raise ValueError(f"max_norm pozitif olmalıdır, verilen: {max_norm}")
        if clip_value is not None and clip_value <= 0:
            raise ValueError(f"clip_value pozitif olmalıdır, verilen: {clip_value}")

        self.max_norm = max_norm
        self.norm_type = norm_type
        self.clip_value = clip_value
        self.raise_on_nan_inf = raise_on_nan_inf

    def inspect_and_clip(self, model: nn.Module) -> GradientCheckResult:
        """Model gradyanlarını inceler, NaN/Inf kontrolü yapar ve gerekirse kırpar."""
        parameters = [p for p in model.parameters() if p.grad is not None]
        if not parameters:
            return GradientCheckResult(
                total_norm=0.0,
                is_clipped=False,
                has_nan_or_inf=False,
                message="Gradyanı olan parametre yok.",
            )

        # 1. NaN / Inf kontrolü
        has_nan_or_inf = False
        for param in parameters:
            assert param.grad is not None
            if torch.isnan(param.grad).any() or torch.isinf(param.grad).any():
                has_nan_or_inf = True
                break

        if has_nan_or_inf:
            if self.raise_on_nan_inf:
                raise ExplodingGradientError(
                    "Gradyan hesaplamasında NaN veya Inf tespit edildi! "
                    "Öğrenme oranını düşürün veya veri normalizasyonunu kontrol edin."
                )
            return GradientCheckResult(
                total_norm=float("nan"),
                is_clipped=False,
                has_nan_or_inf=True,
                message="NaN veya Inf gradyan tespit edildi.",
            )

        # 2. Ham norm hesaplama
        valid_grads = [p.grad for p in parameters if p.grad is not None]
        total_norm_tensor = torch.norm(
            torch.stack([torch.norm(g.detach(), self.norm_type) for g in valid_grads]),
            self.norm_type,
        )
        total_norm = float(total_norm_tensor.item())

        is_clipped = False
        clipped_norm = total_norm

        # 3. Norm kırpma
        if self.max_norm is not None and total_norm > self.max_norm:
            torch.nn.utils.clip_grad_norm_(
                parameters, max_norm=self.max_norm, norm_type=self.norm_type
            )
            is_clipped = True
            clipped_norm = self.max_norm

        # 4. Değer kırpma (opsiyonel)
        if self.clip_value is not None:
            torch.nn.utils.clip_grad_value_(parameters, clip_value=self.clip_value)
            is_clipped = True

        return GradientCheckResult(
            total_norm=total_norm,
            is_clipped=is_clipped,
            has_nan_or_inf=False,
            clipped_norm=clipped_norm,
            message="Gradyanlar normal sınır içinde." if not is_clipped else "Gradyan kırpıldı.",
        )


class EarlyStopping:
    """Doğrulama kaybına veya metriğe göre eğitimi erken durduran mekanizma.

    Özellikler:
    - Belirtilen sabır (patience) kadar gelişme olmazsa durma bayrağı tetikler.
    - min_delta toleransı altında kalan küçük oynamaları gelişme saymaz.
    - 'min' (örn. val_loss) ve 'max' (örn. accuracy, sharpe) modlarını destekler.
    - En iyi model ağırlıklarını bellekte kopyalayarak geri yükleyebilir (restore_best_weights).
    """

    def __init__(
        self,
        patience: int = 5,
        min_delta: float = 1e-4,
        mode: str = "min",
        restore_best_weights: bool = True,
    ) -> None:
        if patience < 1:
            raise ValueError(f"patience >= 1 olmalıdır, verilen: {patience}")
        if min_delta < 0:
            raise ValueError(f"min_delta >= 0 olmalıdır, verilen: {min_delta}")
        if mode not in ("min", "max"):
            raise ValueError(f"mode 'min' veya 'max' olmalıdır, verilen: {mode}")

        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.restore_best_weights = restore_best_weights

        self.counter: int = 0
        self.best_score: float = float("inf") if mode == "min" else float("-inf")
        self.best_epoch: int = 0
        self.early_stop: bool = False
        self.best_weights: dict[str, torch.Tensor] | None = None
        self.stopped_epoch: int | None = None

    def step(self, current_metric: float, model: nn.Module | None = None, epoch: int = 0) -> bool:
        """Her epoch sonunda çağrılır; durma bayrağı döner.

        Args:
            current_metric: Doğrulama kaybı veya hedef metrik skoru.
            model: Opsiyonel PyTorch modeli (en iyi ağırlıkları saklamak için).
            epoch: Mevcut epoch indeksi.

        Returns:
            Eğitim durmalıysa True, devam etmeliyse False.
        """
        if math.isnan(current_metric):
            # NaN skoru gelirse doğrudan durdur
            self.early_stop = True
            self.stopped_epoch = epoch
            return True

        improved = False
        if self.mode == "min":
            if current_metric < (self.best_score - self.min_delta):
                improved = True
        else:
            if current_metric > (self.best_score + self.min_delta):
                improved = True

        if improved:
            self.best_score = current_metric
            self.best_epoch = epoch
            self.counter = 0
            if model is not None and self.restore_best_weights:
                # CPU kopyası olarak sakla (GPU bellek tasarrufu)
                self.best_weights = {
                    k: v.cpu().clone().detach() for k, v in model.state_dict().items()
                }
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
                self.stopped_epoch = epoch

        return self.early_stop

    def restore(self, model: nn.Module) -> None:
        """En iyi ağırlıkları modele geri yükler."""
        if self.restore_best_weights and self.best_weights is not None:
            model.load_state_dict(self.best_weights)


@dataclass
class CheckpointMeta:
    """Checkpoint metadata kaydı."""

    epoch: int
    metrics: dict[str, float]
    created_at: str
    sha256: str
    is_best: bool = False


class CheckpointManager:
    """Model ve eğitim durumu kontrol noktalarını (checkpoint) yöneten sınıf.

    Özellikler:
    - Atomik dosya yazma (geçici dosya -> rename) ile yarım kalma riskini engeller.
    - Model state_dict, optimizer state_dict ve metrikleri güvenle kaydeder.
    - weights_only=True ve SHA-256 hash doğrulamasıyla güvenli yükleme yapar.
    - En iyi (best) kontrol noktasını izole tutar ve en son N kontrol noktasını budar (prune).
    """

    def __init__(
        self,
        base_dir: Path | str,
        max_to_keep: int = 3,
        metric_name: str = "val_loss",
        mode: str = "min",
    ) -> None:
        if max_to_keep < 1:
            raise ValueError(f"max_to_keep >= 1 olmalıdır, verilen: {max_to_keep}")
        if mode not in ("min", "max"):
            raise ValueError(f"mode 'min' veya 'max' olmalıdır, verilen: {mode}")

        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_to_keep = max_to_keep
        self.metric_name = metric_name
        self.mode = mode

        self.best_metric: float = float("inf") if mode == "min" else float("-inf")
        self.best_checkpoint_path: Path | None = None
        self._checkpoints: list[tuple[int, Path]] = []

    def save_checkpoint(
        self,
        model: nn.Module,
        epoch: int,
        metrics: dict[str, float],
        optimizer: torch.optim.Optimizer | None = None,
        scheduler: Any | None = None,
        is_best: bool | None = None,
        filename: str | None = None,
    ) -> Path:
        """Bir kontrol noktası kaydeder."""
        if filename is not None:
            _validate_safe_name(filename, "filename")
            out_filename = filename
        else:
            out_filename = f"checkpoint_epoch_{epoch:04d}.pt"

        checkpoint_path = self.base_dir / out_filename
        temp_path = self.base_dir / f"{out_filename}.tmp"

        payload: dict[str, Any] = {
            "epoch": epoch,
            "metrics": metrics,
            "created_at": datetime.now(UTC).isoformat(),
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict() if optimizer is not None else None,
            "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
        }

        # Atomik yazma
        torch.save(payload, temp_path)
        if temp_path.exists():
            temp_path.replace(checkpoint_path)

        file_hash = compute_file_sha256(checkpoint_path)

        # Meta kaydı
        meta = CheckpointMeta(
            epoch=epoch,
            metrics=metrics,
            created_at=payload["created_at"],
            sha256=file_hash,
            is_best=False,
        )

        meta_path = checkpoint_path.with_suffix(".json")
        meta_temp_path = meta_path.with_suffix(".json.tmp")
        with open(meta_temp_path, "w", encoding="utf-8") as f:
            json.dump(meta.__dict__, f, indent=2)
        meta_temp_path.replace(meta_path)

        # En iyi kontrolü
        curr_metric = metrics.get(self.metric_name)
        if is_best is None and curr_metric is not None:
            if self.mode == "min" and curr_metric < self.best_metric:
                is_best = True
            elif self.mode == "max" and curr_metric > self.best_metric:
                is_best = True
            else:
                is_best = False

        if is_best and curr_metric is not None:
            self.best_metric = curr_metric
            best_path = self.base_dir / "best_model.pt"
            torch.save(payload, temp_path)
            temp_path.replace(best_path)

            best_meta = CheckpointMeta(
                epoch=epoch,
                metrics=metrics,
                created_at=payload["created_at"],
                sha256=compute_file_sha256(best_path),
                is_best=True,
            )
            with open(self.base_dir / "best_model.json", "w", encoding="utf-8") as f:
                json.dump(best_meta.__dict__, f, indent=2)
            self.best_checkpoint_path = best_path

        # Listeye ekle ve buda
        self._checkpoints.append((epoch, checkpoint_path))
        self._prune_checkpoints()

        return checkpoint_path

    def _prune_checkpoints(self) -> None:
        """max_to_keep sınırını aşan eski kontrol noktalarını siler."""
        while len(self._checkpoints) > self.max_to_keep:
            _, old_path = self._checkpoints.pop(0)
            if old_path.exists():
                old_path.unlink()
            meta_path = old_path.with_suffix(".json")
            if meta_path.exists():
                meta_path.unlink()

    def load_checkpoint(
        self,
        checkpoint_path: Path | str,
        model: nn.Module,
        optimizer: torch.optim.Optimizer | None = None,
        scheduler: Any | None = None,
        verify_sha256: bool = True,
    ) -> dict[str, Any]:
        """Kontrol noktasını güvenli şekilde yükler."""
        path = Path(checkpoint_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint bulunamadı: {path}")

        # Meta dosyasındaki SHA256 ile doğrula
        meta_path = path.with_suffix(".json")
        if verify_sha256 and meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                meta_data = json.load(f)
            expected_hash = meta_data.get("sha256")
            actual_hash = compute_file_sha256(path)
            if expected_hash and actual_hash != expected_hash:
                raise ModelIntegrityError(
                    f"Checkpoint SHA-256 bütünlük doğrulaması başarısız! "
                    f"Beklenen: {expected_hash}, Hesaplanan: {actual_hash}"
                )

        # weights_only=True ile güvenli yükleme (güvensiz pickle kod yürütme engellenir)
        try:
            checkpoint_data = torch.load(path, weights_only=True)
        except Exception as exc:
            raise ModelSecurityError(
                f"Checkpoint güvenli (weights_only=True) yüklenemedi: {exc}"
            ) from exc

        model.load_state_dict(checkpoint_data["model_state_dict"])
        if optimizer is not None and checkpoint_data.get("optimizer_state_dict") is not None:
            optimizer.load_state_dict(checkpoint_data["optimizer_state_dict"])
        if scheduler is not None and checkpoint_data.get("scheduler_state_dict") is not None:
            scheduler.load_state_dict(checkpoint_data["scheduler_state_dict"])

        return {
            "epoch": checkpoint_data["epoch"],
            "metrics": checkpoint_data["metrics"],
            "created_at": checkpoint_data.get("created_at"),
        }


@dataclass
class TrainingLoopConfig:
    """Model eğitim döngüsü yapılandırması."""

    epochs: int = 50
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    seed: int = 42
    grad_max_norm: float | None = 1.0
    grad_clip_value: float | None = None
    early_stopping_patience: int = 5
    early_stopping_min_delta: float = 1e-4
    early_stopping_mode: str = "min"
    restore_best_weights: bool = True
    checkpoint_dir: Path | str | None = None
    max_checkpoints_to_keep: int = 3
    track_history: bool = True


@dataclass
class TrainingResult:
    """Eğitim tamamlanma sonucu."""

    total_epochs: int
    best_epoch: int
    best_metric: float
    stopped_early: bool
    stopped_epoch: int | None
    history: dict[str, list[float]] = field(default_factory=dict)
    best_checkpoint_path: Path | None = None
    checkpoint_manager: CheckpointManager | None = None


def get_default_device() -> torch.device:
    """CUDA mimarisi (sm_xxx) uyumluysa CUDA, değilse güvenli biçimde CPU cihazı döner."""
    if torch.cuda.is_available():
        try:
            # Uyumsuz GPU mimarisinde (ör. henüz derlenmemiş sm_120) çekirdek hatasını yakala
            probe = torch.zeros(1, device="cuda")
            _ = probe + 1.0
            del probe
            return torch.device("cuda")
        except Exception:
            return torch.device("cpu")
    return torch.device("cpu")


def train_model(
    model: nn.Module,
    train_loader: DataLoader[Any],
    val_loader: DataLoader[Any],
    criterion: nn.Module,
    optimizer: torch.optim.Optimizer | None = None,
    scheduler: Any | None = None,
    config: TrainingLoopConfig | None = None,
    device: torch.device | str | None = None,
) -> TrainingResult:
    """PyTorch modelini early stopping, gradient denetimi ve checkpointing ile eğitir.

    Args:
        model: Eğitilecek PyTorch modeli.
        train_loader: Eğitim verisi DataLoader.
        val_loader: Doğrulama verisi DataLoader.
        criterion: Kayıp fonksiyonu (ör. nn.MSELoss, nn.BCEWithLogitsLoss).
        optimizer: Optimizer (None ise AdamW kullanılır).
        scheduler: Opsiyonel LR zamanlayıcı.
        config: Eğitim yapılandırması.
        device: Cihaz (cpu / cuda).

    Returns:
        TrainingResult nesnesi.
    """
    cfg = config or TrainingLoopConfig()
    seed_everything(cfg.seed)

    selected_device = torch.device(device) if device is not None else get_default_device()

    model = model.to(selected_device)
    criterion = criterion.to(selected_device)

    opt = optimizer or torch.optim.AdamW(
        model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay
    )

    grad_controller = GradientController(
        max_norm=cfg.grad_max_norm,
        clip_value=cfg.grad_clip_value,
        raise_on_nan_inf=True,
    )

    early_stopping = EarlyStopping(
        patience=cfg.early_stopping_patience,
        min_delta=cfg.early_stopping_min_delta,
        mode=cfg.early_stopping_mode,
        restore_best_weights=cfg.restore_best_weights,
    )

    ckpt_mgr: CheckpointManager | None = None
    if cfg.checkpoint_dir is not None:
        ckpt_mgr = CheckpointManager(
            base_dir=cfg.checkpoint_dir,
            max_to_keep=cfg.max_checkpoints_to_keep,
            metric_name="val_loss",
            mode=cfg.early_stopping_mode,
        )

    history: dict[str, list[float]] = {
        "train_loss": [],
        "val_loss": [],
        "grad_norm": [],
    }

    stopped_early = False

    for epoch in range(1, cfg.epochs + 1):
        # 1. Eğitim adımı
        model.train()
        train_loss_accum = 0.0
        train_batches = 0
        epoch_grad_norms: list[float] = []

        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(selected_device)
            batch_y = batch_y.to(selected_device)

            opt.zero_grad()
            preds = model(batch_x)
            loss = criterion(preds.squeeze(-1), batch_y.squeeze(-1))
            loss.backward()

            # Gradient kontrolü ve clipping
            grad_result = grad_controller.inspect_and_clip(model)
            if not math.isnan(grad_result.total_norm):
                epoch_grad_norms.append(grad_result.total_norm)

            opt.step()

            train_loss_accum += float(loss.item())
            train_batches += 1

        avg_train_loss = train_loss_accum / max(train_batches, 1)
        avg_grad_norm = sum(epoch_grad_norms) / len(epoch_grad_norms) if epoch_grad_norms else 0.0

        # 2. Doğrulama adımı
        model.eval()
        val_loss_accum = 0.0
        val_batches = 0

        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x = batch_x.to(selected_device)
                batch_y = batch_y.to(selected_device)

                preds = model(batch_x)
                loss = criterion(preds.squeeze(-1), batch_y.squeeze(-1))

                val_loss_accum += float(loss.item())
                val_batches += 1

        avg_val_loss = val_loss_accum / max(val_batches, 1)

        if scheduler is not None:
            if isinstance(scheduler, torch.optim.lr_scheduler.ReduceLROnPlateau):
                scheduler.step(avg_val_loss)
            else:
                scheduler.step()

        if cfg.track_history:
            history["train_loss"].append(avg_train_loss)
            history["val_loss"].append(avg_val_loss)
            history["grad_norm"].append(avg_grad_norm)

        # 3. Checkpoint kaydı
        if ckpt_mgr is not None:
            ckpt_mgr.save_checkpoint(
                model=model,
                epoch=epoch,
                metrics={"train_loss": avg_train_loss, "val_loss": avg_val_loss},
                optimizer=opt,
                scheduler=scheduler,
            )

        # 4. Early stopping kontrolü
        should_stop = early_stopping.step(
            current_metric=avg_val_loss,
            model=model,
            epoch=epoch,
        )

        if should_stop:
            stopped_early = True
            break

    # En iyi ağırlıkları geri yükle
    if cfg.restore_best_weights:
        early_stopping.restore(model)

    best_ckpt_path = ckpt_mgr.best_checkpoint_path if ckpt_mgr else None

    return TrainingResult(
        total_epochs=len(history["train_loss"]),
        best_epoch=early_stopping.best_epoch,
        best_metric=early_stopping.best_score,
        stopped_early=stopped_early,
        stopped_epoch=early_stopping.stopped_epoch,
        history=history,
        best_checkpoint_path=best_ckpt_path,
        checkpoint_manager=ckpt_mgr,
    )
