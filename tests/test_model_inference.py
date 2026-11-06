"""Toplu (batch) çıkarım ve TorchScript/ONNX optimizasyonu için kapsamlı testler."""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch

from stock_analysis.models.architectures import CNN1DConfig, MLPConfig, SmallCNN1D, SmallMLP
from stock_analysis.models.inference import (
    BatchInferenceConfig,
    BatchInferenceEngine,
    ExportEvaluationResult,
    InferenceBackend,
    ONNXExporter,
    TorchScriptExporter,
    evaluate_model_export,
    is_onnx_available,
)


@pytest.fixture
def sample_mlp_model() -> tuple[SmallMLP, torch.Tensor]:
    """Test için küçük MLP modeli ve örnek girdi tensörü."""
    config = MLPConfig(input_dim=10, hidden_dims=(32, 16), output_dim=1, dropout=0.0)
    model = SmallMLP(config)
    model.eval()
    example_input = torch.randn(8, 10)
    return model, example_input


@pytest.fixture
def sample_cnn_model() -> tuple[SmallCNN1D, torch.Tensor]:
    """Test için küçük 1D CNN modeli ve örnek girdi tensörü."""
    config = CNN1DConfig(
        in_channels=5,
        seq_len=20,
        num_filters=(16, 32),
        kernel_size=3,
        output_dim=1,
        dropout=0.0,
    )
    model = SmallCNN1D(config)
    model.eval()
    example_input = torch.randn(8, 20, 5)
    return model, example_input


def test_batch_inference_config_validation() -> None:
    """Geçersiz yapılandırma değerlerinin doğrulanması."""
    with pytest.raises(ValueError, match="batch_size"):
        BatchInferenceConfig(batch_size=0)

    with pytest.raises(ValueError, match="cache_ttl_seconds"):
        BatchInferenceConfig(cache_ttl_seconds=0)

    valid = BatchInferenceConfig(batch_size=32, cache_ttl_seconds=1800)
    assert valid.batch_size == 32
    assert valid.device == "cpu"


def test_torchscript_export_and_load_mlp(sample_mlp_model: tuple[SmallMLP, torch.Tensor]) -> None:
    """MLP modelinin TorchScript formatına aktarılması ve sayısal eşdeğerliği."""
    model, example_input = sample_mlp_model

    with tempfile.TemporaryDirectory() as tmp_dir:
        export_path = Path(tmp_dir) / "mlp_traced.pt"
        saved_path = TorchScriptExporter.export_model(
            model=model,
            example_input=example_input,
            output_path=export_path,
            method="trace",
        )
        assert saved_path.exists()
        assert saved_path.stat().st_size > 0

        # Modeli geri yükleme
        loaded_model = TorchScriptExporter.load_model(saved_path, device="cpu")

        # Farklı batch boyutunda test (dinamik batch)
        test_input = torch.randn(15, 10)
        with torch.no_grad():
            orig_out = model(test_input)
            ts_out = loaded_model(test_input)

        assert torch.allclose(orig_out, ts_out, atol=1e-5)


def test_torchscript_export_and_load_cnn(sample_cnn_model: tuple[SmallCNN1D, torch.Tensor]) -> None:
    """1D CNN modelinin TorchScript formatına aktarılması ve sayısal eşdeğerliği."""
    model, example_input = sample_cnn_model

    with tempfile.TemporaryDirectory() as tmp_dir:
        export_path = Path(tmp_dir) / "cnn_traced.pt"
        saved_path = TorchScriptExporter.export_model(
            model=model,
            example_input=example_input,
            output_path=export_path,
            method="trace",
        )
        assert saved_path.exists()

        loaded_model = TorchScriptExporter.load_model(saved_path, device="cpu")
        test_input = torch.randn(4, 20, 5)

        with torch.no_grad():
            orig_out = model(test_input)
            ts_out = loaded_model(test_input)

        assert torch.allclose(orig_out, ts_out, atol=1e-5)


def test_torchscript_load_non_existent_file() -> None:
    """Var olmayan model dosyası yüklendiğinde FileNotFoundError fırlatılmalıdır."""
    with pytest.raises(FileNotFoundError):
        TorchScriptExporter.load_model("non_existent_model.pt")


def test_onnx_export(sample_mlp_model: tuple[SmallMLP, torch.Tensor]) -> None:
    """Modelin ONNX formatına aktarımı veya eksik bağımlılık koruması kontrolü."""
    model, example_input = sample_mlp_model

    with tempfile.TemporaryDirectory() as tmp_dir:
        export_path = Path(tmp_dir) / "model.onnx"
        if not is_onnx_available():
            with pytest.raises(RuntimeError, match="ONNX kütüphanesi ortamda kurulu değil"):
                ONNXExporter.export_model(
                    model=model,
                    example_input=example_input,
                    output_path=export_path,
                )
        else:
            saved_path = ONNXExporter.export_model(
                model=model,
                example_input=example_input,
                output_path=export_path,
            )
            assert saved_path.exists()
            assert saved_path.stat().st_size > 0


def test_evaluate_model_export_torchscript(sample_mlp_model: tuple[SmallMLP, torch.Tensor]) -> None:
    """TorchScript dışa aktarım değerlendirmesi ve gecikme metrikleri."""
    model, example_input = sample_mlp_model

    with tempfile.TemporaryDirectory() as tmp_dir:
        eval_result = evaluate_model_export(
            model=model,
            example_input=example_input,
            output_dir=tmp_dir,
            backend=InferenceBackend.TORCHSCRIPT,
            benchmark_runs=5,
        )

        assert isinstance(eval_result, ExportEvaluationResult)
        assert eval_result.backend == InferenceBackend.TORCHSCRIPT
        assert eval_result.is_numerically_equivalent is True
        assert eval_result.max_absolute_diff < 1e-4
        assert eval_result.cpu_latency_p95_ms >= 0.0
        assert eval_result.throughput_samples_per_sec > 0.0


def test_evaluate_model_export_onnx(sample_mlp_model: tuple[SmallMLP, torch.Tensor]) -> None:
    """ONNX dışa aktarım değerlendirmesi kontrolü."""
    model, example_input = sample_mlp_model

    with tempfile.TemporaryDirectory() as tmp_dir:
        if not is_onnx_available():
            with pytest.raises(RuntimeError, match="ONNX kütüphanesi ortamda kurulu değil"):
                evaluate_model_export(
                    model=model,
                    example_input=example_input,
                    output_dir=tmp_dir,
                    backend=InferenceBackend.ONNX,
                    benchmark_runs=5,
                )
        else:
            eval_result = evaluate_model_export(
                model=model,
                example_input=example_input,
                output_dir=tmp_dir,
                backend=InferenceBackend.ONNX,
                benchmark_runs=5,
            )
            assert eval_result.backend == InferenceBackend.ONNX
            assert eval_result.is_numerically_equivalent is True
            assert eval_result.file_size_bytes > 0


def test_batch_inference_engine_predict_batch(
    sample_mlp_model: tuple[SmallMLP, torch.Tensor],
) -> None:
    """Toplu çıkarım motorunda parçalı (chunked) batch işleme ve metrikler."""
    model, _ = sample_mlp_model
    config = BatchInferenceConfig(batch_size=10, num_threads=2)
    engine = BatchInferenceEngine(model=model, model_version="v1.0.0", config=config)

    # 35 örnek (10'ar parçalar halinde 4 batch)
    inputs = np.random.randn(35, 10).astype(np.float32)
    result = engine.predict_batch(inputs)

    assert result.total_samples == 35
    assert result.batch_count == 4
    assert result.predictions.shape == (35, 1)
    assert result.latency_ms > 0
    assert result.avg_latency_per_sample_ms > 0
    assert result.model_version == "v1.0.0"

    # Boş girdi testi
    empty_res = engine.predict_batch(np.empty((0, 10)))
    assert empty_res.total_samples == 0
    assert empty_res.batch_count == 0


def test_batch_inference_engine_predict_for_symbols(
    sample_mlp_model: tuple[SmallMLP, torch.Tensor],
) -> None:
    """Sembol bazlı toplu tahmin üretimi ve önbellek mekanizması testi."""
    model, _ = sample_mlp_model
    config = BatchInferenceConfig(batch_size=16, enable_cache=True, cache_ttl_seconds=3600)
    engine = BatchInferenceEngine(model=model, model_version="v1.0.0", config=config)

    symbol_data = {
        "THYAO": np.random.randn(10).astype(np.float32),
        "GARAN": np.random.randn(10).astype(np.float32),
        "ASELS": np.random.randn(10).astype(np.float32),
    }

    # İlk çağrı: Önbellek boş, miss olmalı
    preds1 = engine.predict_for_symbols(symbol_data)
    assert len(preds1) == 3
    assert "THYAO" in preds1
    stats1 = engine.get_cache_stats()
    assert stats1["cached_entries"] == 3
    assert stats1["hits"] == 0
    assert stats1["misses"] == 3

    # İkinci çağrı: Aynı semboller, cache hit olmalı
    preds2 = engine.predict_for_symbols(symbol_data)
    assert np.array_equal(preds1["THYAO"], preds2["THYAO"])
    stats2 = engine.get_cache_stats()
    assert stats2["hits"] == 3
    assert stats2["hit_ratio"] == 0.5

    # Önbelleği temizleme
    engine.clear_cache()
    stats3 = engine.get_cache_stats()
    assert stats3["cached_entries"] == 0
