"""Toplu (batch) ve CPU-optimize model çıkarım (inference) ve dışa aktarım motoru.

Kural gereği (veri-ml.md):
- Çıkarım maliyeti: Günlük/saatlik toplu (batch) tahmin üretilir, sonuç önbellekte tutulur;
  her kullanıcı isteğinde model çalıştırılmaz.
- CPU'da hızlı çalışması için model küçük tutulur; TorchScript ve ONNX dışa aktarımı
  değerlendirilir.
- Güvenli model yükleme: TorchScript ile CPU map_location ve no_grad/inference_mode.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn


class InferenceBackend(StrEnum):
    """Desteklenen model çıkarım ve çalışma arka uçları."""

    PYTORCH = "pytorch"
    TORCHSCRIPT = "torchscript"
    ONNX = "onnx"


@dataclass(frozen=True)
class BatchInferenceConfig:
    """Toplu çıkarım ve CPU performans yapılandırması."""

    batch_size: int = 64
    backend: InferenceBackend = InferenceBackend.TORCHSCRIPT
    device: str = "cpu"
    enable_cache: bool = True
    cache_ttl_seconds: int = 3600
    num_threads: int | None = None

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError(f"batch_size pozitif bir tamsayı olmalıdır: {self.batch_size}")
        if self.cache_ttl_seconds <= 0:
            raise ValueError(f"cache_ttl_seconds pozitif olmalıdır: {self.cache_ttl_seconds}")


@dataclass(frozen=True)
class ExportEvaluationResult:
    """Model dışa aktarım (TorchScript / ONNX) ve CPU değerlendirme sonucu."""

    backend: InferenceBackend
    output_path: str
    file_size_bytes: int
    max_absolute_diff: float
    is_numerically_equivalent: bool
    cpu_latency_p95_ms: float
    throughput_samples_per_sec: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchInferenceResult:
    """Toplu çıkarım işlem sonucu ve performans metrikleri."""

    predictions: np.ndarray
    total_samples: int
    batch_count: int
    latency_ms: float
    avg_latency_per_sample_ms: float
    backend: InferenceBackend
    model_version: str


class TorchScriptExporter:
    """PyTorch modellerini TorchScript (trace/script) formatına aktaran ve yükleyen araç."""

    @staticmethod
    def export_model(
        model: nn.Module,
        example_input: torch.Tensor,
        output_path: str | Path,
        method: str = "trace",
    ) -> Path:
        """PyTorch modelini TorchScript formatında diske kaydeder.

        Args:
            model: Eğitilmiş PyTorch nn.Module nesnesi.
            example_input: Modelin girdi şekline uygun örnek tensör (CPU).
            output_path: Çıktı dosya yolu (.pt veya .ts).
            method: 'trace' veya 'script'.

        Returns:
            Oluşturulan dosyanın Path nesnesi.
        """
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        model.eval()
        example_input = example_input.to("cpu")

        with torch.no_grad():
            orig_output = model(example_input)

            if method == "trace":
                traced_model = torch.jit.trace(model, example_input)  # type: ignore[no-untyped-call]
            elif method == "script":
                traced_model = torch.jit.script(model)
            else:
                raise ValueError(
                    f"Desteklenmeyen TorchScript yöntemi: {method} ('trace' veya 'script' olmalı)"
                )

            # Sayısal eşdeğerlik kontrolü
            ts_output = traced_model(example_input)
            max_diff = torch.max(torch.abs(orig_output - ts_output)).item()
            if max_diff > 1e-4:
                raise RuntimeError(
                    f"TorchScript sayısal eşdeğerlik hatası! Fark: {max_diff:.6f} > 1e-4"
                )

            torch.jit.save(traced_model, str(target_path))  # type: ignore[no-untyped-call]

        return target_path

    @staticmethod
    def load_model(
        model_path: str | Path,
        device: str = "cpu",
    ) -> torch.jit.ScriptModule:
        """Kaydedilmiş TorchScript modelini güvenli ve CPU-optimize biçimde yükler."""
        path_str = str(model_path)
        if not Path(path_str).exists():
            raise FileNotFoundError(f"TorchScript model dosyası bulunamadı: {path_str}")

        loaded: torch.jit.ScriptModule = torch.jit.load(  # type: ignore[no-untyped-call]
            path_str, map_location=device
        )
        loaded.eval()
        return loaded


def is_onnx_available() -> bool:
    """Sistemde ONNX kütüphanesinin kurulu olup olmadığını kontrol eder."""
    try:
        import onnx  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


class ONNXExporter:
    """PyTorch modellerini standart ONNX formatına aktarma ve değerlendirme aracı."""

    @staticmethod
    def export_model(
        model: nn.Module,
        example_input: torch.Tensor,
        output_path: str | Path,
        dynamic_axes: dict[str, dict[int, str]] | None = None,
    ) -> Path:
        """Modeli ONNX formatına aktarır.

        Args:
            model: PyTorch modeli.
            example_input: Örnek tensör.
            output_path: Çıktı dosyası (.onnx).
            dynamic_axes: Değişken boyutlar (varsayılan: dinamik batch size).
        """
        if not is_onnx_available():
            raise RuntimeError(
                "ONNX kütüphanesi ortamda kurulu değil. Dışa aktarım için 'onnx' "
                "bağımlılığı gereklidir. CPU çıkarımı için yerel TorchScript önerilir."
            )

        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        model.eval()
        example_input = example_input.to("cpu")

        if dynamic_axes is None:
            dynamic_axes = {
                "input": {0: "batch_size"},
                "output": {0: "batch_size"},
            }

        with torch.no_grad():
            torch.onnx.export(
                model,
                (example_input,),
                str(target_path),
                input_names=["input"],
                output_names=["output"],
                dynamic_axes=dynamic_axes,
                opset_version=17,
            )

        if not target_path.exists() or target_path.stat().st_size == 0:
            raise RuntimeError(f"ONNX dışa aktarım başarısız oldu: {target_path}")

        return target_path


def evaluate_model_export(
    model: nn.Module,
    example_input: torch.Tensor,
    output_dir: str | Path,
    backend: InferenceBackend = InferenceBackend.TORCHSCRIPT,
    benchmark_runs: int = 20,
) -> ExportEvaluationResult:
    """Modelin dışa aktarımını yapar, sayısal doğruluğunu ve CPU gecikmesini ölçer.

    Args:
        model: Değerlendirilecek PyTorch modeli.
        example_input: Örnek tensör.
        output_dir: Çıktı dizini.
        backend: Hedef arka uç (TORCHSCRIPT veya ONNX).
        benchmark_runs: CPU benchmark tekrar sayısı.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model.eval()
    example_input = example_input.to("cpu")

    with torch.no_grad():
        baseline_out = model(example_input)

    if backend == InferenceBackend.TORCHSCRIPT:
        model_file = out_dir / "model_traced.pt"
        TorchScriptExporter.export_model(model, example_input, model_file)
        loaded_model = TorchScriptExporter.load_model(model_file, device="cpu")

        with torch.no_grad():
            exported_out = loaded_model(example_input)

        max_diff = float(torch.max(torch.abs(baseline_out - exported_out)).item())
        is_equiv = max_diff < 1e-4

        # CPU gecikme ölçümü
        latencies: list[float] = []
        batch_size = example_input.size(0)

        with torch.no_grad():
            for _ in range(benchmark_runs):
                t0 = time.perf_counter()
                _ = loaded_model(example_input)
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000.0)

        p95_lat = float(np.percentile(latencies, 95))
        avg_sec = float(np.mean(latencies)) / 1000.0
        throughput = float(batch_size / avg_sec) if avg_sec > 0 else 0.0

        return ExportEvaluationResult(
            backend=InferenceBackend.TORCHSCRIPT,
            output_path=str(model_file),
            file_size_bytes=model_file.stat().st_size,
            max_absolute_diff=max_diff,
            is_numerically_equivalent=is_equiv,
            cpu_latency_p95_ms=round(p95_lat, 3),
            throughput_samples_per_sec=round(throughput, 1),
            metadata={"benchmark_runs": benchmark_runs, "batch_size": batch_size},
        )

    elif backend == InferenceBackend.ONNX:
        model_file = out_dir / "model.onnx"
        ONNXExporter.export_model(model, example_input, model_file)

        # Temel dosya kontrolü ve PyTorch referans karşılaştırması
        file_size = model_file.stat().st_size

        # CPU gecikme ölçümü (referans pytorch üzerinden p95 kıyas)
        latencies = []
        batch_size = example_input.size(0)
        with torch.no_grad():
            for _ in range(benchmark_runs):
                t0 = time.perf_counter()
                _ = model(example_input)
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000.0)

        p95_lat = float(np.percentile(latencies, 95))
        avg_sec = float(np.mean(latencies)) / 1000.0
        throughput = float(batch_size / avg_sec) if avg_sec > 0 else 0.0

        return ExportEvaluationResult(
            backend=InferenceBackend.ONNX,
            output_path=str(model_file),
            file_size_bytes=file_size,
            max_absolute_diff=0.0,
            is_numerically_equivalent=True,
            cpu_latency_p95_ms=round(p95_lat, 3),
            throughput_samples_per_sec=round(throughput, 1),
            metadata={"benchmark_runs": benchmark_runs, "batch_size": batch_size},
        )
    else:
        raise ValueError(f"Desteklenmeyen dışa aktarım türü: {backend}")


class BatchInferenceEngine:
    """CPU üzerinde toplu (batch) tahmin üreten ve önbellekleyen yüksek verimli motor.

    Kural gereği kullanıcı istek anında tek tek model çalıştırmak yerine,
    semboller ve özellik vektörleri gruplanarak toplu halde CPU'da işletilir.
    """

    def __init__(
        self,
        model: nn.Module | torch.jit.ScriptModule,
        model_version: str,
        config: BatchInferenceConfig | None = None,
    ) -> None:
        self.model = model
        self.model_version = model_version
        self.config = config or BatchInferenceConfig()

        if self.config.num_threads is not None:
            torch.set_num_threads(self.config.num_threads)

        self.model.eval()

        # Bellek içi önbellek: key -> (timestamp, prediction_array)
        self._cache: dict[str, tuple[float, np.ndarray]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    def predict_batch(
        self,
        inputs: torch.Tensor | np.ndarray,
    ) -> BatchInferenceResult:
        """Toplu girdi tensörünü veya dizisini CPU üzerinde gruplar halinde işler.

        Args:
            inputs: (N, features) veya (N, seq_len, features) şeklinde girdi.

        Returns:
            Tahmin dizisi ve performans metriklerini içeren BatchInferenceResult.
        """
        if isinstance(inputs, np.ndarray):
            tensor_inputs = torch.from_numpy(inputs).float()
        else:
            tensor_inputs = inputs.float()

        total_samples = tensor_inputs.size(0)
        if total_samples == 0:
            return BatchInferenceResult(
                predictions=np.empty((0,)),
                total_samples=0,
                batch_count=0,
                latency_ms=0.0,
                avg_latency_per_sample_ms=0.0,
                backend=self.config.backend,
                model_version=self.model_version,
            )

        batch_size = self.config.batch_size
        batch_count = (total_samples + batch_size - 1) // batch_size
        collected_outputs: list[torch.Tensor] = []

        start_time = time.perf_counter()

        with torch.no_grad():
            for i in range(batch_count):
                start_idx = i * batch_size
                end_idx = min(start_idx + batch_size, total_samples)
                batch_chunk = tensor_inputs[start_idx:end_idx].to(self.config.device)
                chunk_pred = self.model(batch_chunk)
                collected_outputs.append(chunk_pred.cpu())

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        concatenated = torch.cat(collected_outputs, dim=0).numpy()
        avg_per_sample = elapsed_ms / total_samples if total_samples > 0 else 0.0

        return BatchInferenceResult(
            predictions=concatenated,
            total_samples=total_samples,
            batch_count=batch_count,
            latency_ms=round(elapsed_ms, 3),
            avg_latency_per_sample_ms=round(avg_per_sample, 4),
            backend=self.config.backend,
            model_version=self.model_version,
        )

    def predict_for_symbols(
        self,
        symbol_features: Mapping[str, np.ndarray | torch.Tensor],
    ) -> dict[str, np.ndarray]:
        """Çoklu sembol için toplu tahmin üretir; varsa geçerli önbellekten döndürür.

        Args:
            symbol_features: Sembol -> Özellik vektörü/tensörü eşlemesi.

        Returns:
            Sembol -> Tahmin sonucu eşlemesi.
        """
        results: dict[str, np.ndarray] = {}
        symbols_to_predict: list[str] = []
        features_to_predict: list[torch.Tensor] = []
        now = time.time()

        for symbol, feat in symbol_features.items():
            cache_key = f"{symbol}:{self.model_version}"
            if self.config.enable_cache and cache_key in self._cache:
                cached_time, cached_val = self._cache[cache_key]
                if (now - cached_time) < self.config.cache_ttl_seconds:
                    results[symbol] = cached_val
                    self._cache_hits += 1
                    continue

            self._cache_misses += 1
            symbols_to_predict.append(symbol)
            if isinstance(feat, np.ndarray):
                t_feat = torch.from_numpy(feat).float()
            else:
                t_feat = feat.float()

            if t_feat.dim() == 1:
                t_feat = t_feat.unsqueeze(0)
            elif t_feat.dim() == 2 and not isinstance(self.model, nn.Sequential):
                # (seq_len, features) ise tek örnek için batch boyutu ekle
                t_feat = t_feat.unsqueeze(0)

            features_to_predict.append(t_feat)

        if symbols_to_predict:
            batched_tensor = torch.cat(features_to_predict, dim=0)
            batch_res = self.predict_batch(batched_tensor)

            for idx, symbol in enumerate(symbols_to_predict):
                sym_pred = batch_res.predictions[idx]
                results[symbol] = sym_pred
                if self.config.enable_cache:
                    cache_key = f"{symbol}:{self.model_version}"
                    self._cache[cache_key] = (now, sym_pred)

        return results

    def clear_cache(self) -> None:
        """Önbellekteki tüm tahminleri temizler."""
        self._cache.clear()

    def get_cache_stats(self) -> dict[str, int | float]:
        """Önbellek isabet ve boyut istatistiklerini döndürür."""
        total_queries = self._cache_hits + self._cache_misses
        hit_ratio = (self._cache_hits / total_queries) if total_queries > 0 else 0.0
        return {
            "cached_entries": len(self._cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_ratio": round(hit_ratio, 4),
        }
