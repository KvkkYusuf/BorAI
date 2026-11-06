"""Model yükleme güvenliği, hash doğrulama ve safetensors altyapısı.

FAZ-11 v2 gereksinimlerini karşılar:
- Rastgele kod çalıştırma açıklarını önlemek için safetensors ve weights_only zorunluluğu.
- Güvensiz/ham pickle (.pkl, .pickle) dosyalarının engellenmesi.
- Kötü amaçlı pickle opcode'ları ve path traversal saldırılarına karşı güvenlik taraması.
- SHA-256 kriptografik bütünlük doğrulaması (ağırlıklar, meta veri ve kontrol noktaları).
- Sıfır bağımlılıklı safetensors serileştirme ve opsiyonel 'safetensors' kütüphane köprüsü.
"""

from __future__ import annotations

import io
import json
import pickletools
import re
import struct
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import torch
import torch.nn as nn


class ModelRegistryError(Exception):
    """Model kayıt ve güvenlik altyapısı temel hata sınıfı."""


class ModelSecurityError(ModelRegistryError):
    """Zararlı kod, path traversal veya güvensiz model dosyası tespit edildiğinde fırlatılır."""


class ModelIntegrityError(ModelRegistryError):
    """SHA-256 hash doğrulaması veya dosya bütünlüğü başarısız olduğunda fırlatılır."""


# İzin verilen dosya uzantıları
SAFE_WEIGHTS_EXTENSIONS: tuple[str, ...] = (".safetensors", ".pt", ".pth", ".bin")
FORBIDDEN_EXTENSIONS: tuple[str, ...] = (
    ".pkl",
    ".pickle",
    ".py",
    ".sh",
    ".bat",
    ".exe",
    ".cmd",
    ".dll",
    ".so",
)

# Bilinen tehlikeli Python modülleri ve fonksiyonları (pickle opcode taraması için)
DISALLOWED_PICKLE_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "posix",
        "nt",
        "subprocess",
        "sys",
        "shutil",
        "builtins.eval",
        "builtins.exec",
        "builtins.__import__",
        "builtins.compile",
        "builtins.open",
        "socket",
        "pty",
        "platform",
        "importlib",
        "requests",
        "urllib",
        "http",
        "webbrowser",
    }
)

# Safetensors veri tipi eşlemeleri
TORCH_TO_SAFETENSORS_DTYPE: dict[torch.dtype, str] = {
    torch.float32: "F32",
    torch.float64: "F64",
    torch.float16: "F16",
    torch.bfloat16: "BF16",
    torch.int64: "I64",
    torch.int32: "I32",
    torch.int16: "I16",
    torch.int8: "I8",
    torch.uint8: "U8",
    torch.bool: "BOOL",
}

SAFETENSORS_TO_TORCH_DTYPE: dict[str, torch.dtype] = {
    v: k for k, v in TORCH_TO_SAFETENSORS_DTYPE.items()
}

_SAFE_PATH_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def validate_safe_filename(name: str, param_name: str = "filename") -> None:
    """Path traversal ve geçersiz karakterleri engeller."""
    if (
        not name
        or not _SAFE_PATH_NAME_PATTERN.match(name)
        or ".." in name
        or "/" in name
        or "\\" in name
    ):
        raise ModelSecurityError(
            f"Geçersiz veya güvensiz {param_name}: '{name}'. "
            "Yalnızca alfanümerik, tire, alt çizgi ve nokta içerebilir."
        )


def compute_file_sha256(file_path: Path | str) -> str:
    """Dosyanın SHA-256 kriptografik özetini hesaplar."""
    import hashlib

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Dosya bulunamadı: {path}")

    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_file_sha256(file_path: Path | str, expected_sha256: str) -> None:
    """Dosyanın SHA-256 özetini beklenen değerle karşılaştırır, uyuşmazlıkta hata fırlatır."""
    actual_sha256 = compute_file_sha256(file_path)
    if actual_sha256.lower() != expected_sha256.lower():
        raise ModelIntegrityError(
            f"Ağırlık dosyasının SHA-256 özeti eşleşmiyor ({file_path})! "
            f"Beklenen: {expected_sha256}, Gerçekleşen: {actual_sha256}. "
            "Bütünlük doğrulaması başarısız; dosya bozulmuş, eksik veya kurcalanmış olabilir."
        )


# ==============================================================================
# SAFETENSORS YAZMA VE OKUMA MOTORU (Sıfır Dış Bağımlılık + Kütüphane Köprüsü)
# ==============================================================================


def save_safetensors(
    tensors: Mapping[str, torch.Tensor],
    file_path: Path | str,
    metadata: Mapping[str, str] | None = None,
) -> str:
    """Tensörleri güvenli, unpickled HuggingFace uyumlu safetensors formatında kaydeder.

    Varsa 'safetensors' harici kütüphanesini kullanır; yoksa safetensors belirtimine
    (8-byte little endian header length, UTF-8 JSON header, 8-byte alignment, ham byte dizilimi)
    uygun yerel safetensors motorunu çalıştırır.

    Args:
        tensors: Katman ismi -> PyTorch tensörü eşlemesi.
        file_path: Kaydedilecek hedef dosya yolu.
        metadata: İsteğe bağlı üst veri anahtar-değer sözlüğü.

    Returns:
        Oluşturulan safetensors dosyasının SHA-256 kriptografik özeti.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Resmi safetensors kütüphanesi yüklüyse öncelikli olarak kullan
    try:
        import safetensors.torch as st_torch  # type: ignore[import-not-found]

        st_meta = dict(metadata) if metadata else None
        st_torch.save_file(dict(tensors), str(path), metadata=st_meta)
        return compute_file_sha256(path)
    except ImportError:
        pass

    # Yerel safetensors serileştirici (Zero dependency, spec-compliant)
    header: dict[str, Any] = {}
    if metadata:
        header["__metadata__"] = {str(k): str(v) for k, v in metadata.items()}

    buffers: list[bytes] = []
    current_offset = 0

    for name, tensor in tensors.items():
        if not isinstance(tensor, torch.Tensor):
            raise TypeError(f"'{name}' geçerli bir torch.Tensor değil: {type(tensor)}")

        # Tensörü CPU'ya al, contiguous yap ve byte formatına dönüştür
        t_cpu = tensor.detach().cpu().contiguous()
        dtype_str = TORCH_TO_SAFETENSORS_DTYPE.get(t_cpu.dtype)
        if dtype_str is None:
            raise ValueError(
                f"Desteklenmeyen tensör tipi '{t_cpu.dtype}' (katman: {name}). "
                f"Desteklenenler: {list(TORCH_TO_SAFETENSORS_DTYPE.keys())}"
            )

        flat_tensor = t_cpu.flatten() if t_cpu.dim() == 0 else t_cpu
        tensor_bytes = flat_tensor.view(torch.uint8).numpy().tobytes()
        byte_len = len(tensor_bytes)

        header[name] = {
            "dtype": dtype_str,
            "shape": list(t_cpu.shape),
            "data_offsets": [current_offset, current_offset + byte_len],
        }
        current_offset += byte_len
        buffers.append(tensor_bytes)

    header_json = json.dumps(header, separators=(",", ":"))
    header_bytes = header_json.encode("utf-8")

    # Safetensors 8-bayt hizalama (alignment) kuralı: (8 + len(header)) % 8 == 0
    padding_needed = (8 - (len(header_bytes) % 8)) % 8
    if padding_needed > 0:
        header_bytes += b" " * padding_needed

    header_len = len(header_bytes)

    with path.open("wb") as f:
        f.write(struct.pack("<Q", header_len))
        f.write(header_bytes)
        for buf in buffers:
            f.write(buf)

    return compute_file_sha256(path)


def load_safetensors(
    file_path: Path | str,
    device: str | torch.device = "cpu",
) -> dict[str, torch.Tensor]:
    """Safetensors dosyasından tensörleri güvenli ve sıfır-kod çalıştırmalı olarak yükler."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Safetensors dosyası bulunamadı: {path}")

    # Harici kütüphane varsa kullan
    try:
        import safetensors.torch as st_torch

        loaded = st_torch.load_file(str(path), device=str(device))
        return {str(k): v for k, v in loaded.items()}
    except ImportError:
        pass

    # Yerel safetensors ayrıştırıcı
    with path.open("rb") as f:
        header_len_bytes = f.read(8)
        if len(header_len_bytes) < 8:
            raise ModelSecurityError(f"Geçersiz safetensors başlığı (dosya çok kısa): {path}")

        (header_len,) = struct.unpack("<Q", header_len_bytes)
        if header_len > 100 * 1024 * 1024:  # 100MB başlık sınırı (DoS koruması)
            raise ModelSecurityError(f"Aşırı büyük safetensors başlık uzunluğu: {header_len} bayt")

        header_bytes = f.read(header_len)
        if len(header_bytes) < header_len:
            raise ModelSecurityError("Safetensors başlık baytları eksik veya dosya bozuk.")

        header = json.loads(header_bytes.decode("utf-8"))
        data_bytes = f.read()

    target_device = torch.device(device)
    result: dict[str, torch.Tensor] = {}

    for name, info in header.items():
        if name == "__metadata__":
            continue

        dtype_str = info.get("dtype")
        shape = info.get("shape")
        offsets = info.get("data_offsets")

        if not dtype_str or shape is None or offsets is None:
            raise ModelSecurityError(f"Geçersiz tensör meta verisi: {name}")

        torch_dtype = SAFETENSORS_TO_TORCH_DTYPE.get(dtype_str)
        if torch_dtype is None:
            raise ValueError(f"Bilinmeyen safetensors veri tipi: {dtype_str}")

        start_off, end_off = offsets
        if start_off < 0 or end_off > len(data_bytes) or start_off > end_off:
            raise ModelSecurityError(f"Geçersiz veri ofseti aralığı [{start_off}, {end_off}]")

        raw_slice = data_bytes[start_off:end_off]
        tensor = (
            torch.frombuffer(bytearray(raw_slice), dtype=torch.uint8)
            .view(torch_dtype)
            .reshape(shape)
            .clone()
        )

        if target_device.type != "cpu":
            tensor = tensor.to(target_device)

        result[name] = tensor

    return result


# ==============================================================================
# PICKLE GÜVENLİK DENETİMİ VE GÜVENLİ AĞIRLIK YÜKLEYİCİ
# ==============================================================================


def inspect_pickle_stream_safety(stream: io.BytesIO | bytes) -> None:
    """Pickle opcode akışını tarayarak yetkisiz kod yürütme çağrılarını denetler."""
    raw_data = stream.getvalue() if isinstance(stream, io.BytesIO) else stream
    try:
        for opcode, arg, _pos in pickletools.genops(raw_data):
            if opcode.name in ("GLOBAL", "STACK_GLOBAL"):
                if arg:
                    module_name = str(arg).split()[0]
                    for bad in DISALLOWED_PICKLE_MODULES:
                        if module_name == bad or module_name.startswith(bad + "."):
                            raise ModelSecurityError(
                                f"Güvensiz veya zararlı pickle talimatı tespit edildi! "
                                f"Yasaklı modül: '{arg}'"
                            )
    except Exception as e:
        if isinstance(e, ModelSecurityError):
            raise
        # Format tam saf pickle değilse veya çözülemiyorsa
        return


def inspect_weights_file_security(
    file_path: Path | str,
    max_file_size_bytes: int = 1024 * 1024 * 1024,  # 1 GB
) -> None:
    """Ağırlık dosyasının uzantısını, boyutunu ve içeriğini güvensiz desenlere karşı denetler."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Model dosyası bulunamadı: {path}")

    # Uzantı denetimi
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_EXTENSIONS:
        raise ModelSecurityError(
            f"Ham pickle veya çalıştırılabilir dosya uzantısı ('{suffix}') yasaklanmıştır. "
            "Güvenlik için .safetensors veya weights_only=True uyumlu .pt/.pth kullanınız."
        )

    if suffix not in SAFE_WEIGHTS_EXTENSIONS:
        raise ModelSecurityError(
            f"Desteklenmeyen model ağırlık uzantısı: '{suffix}'. "
            f"İzin verilenler: {SAFE_WEIGHTS_EXTENSIONS}"
        )

    # Boyut denetimi (DoS ve zip-bomb koruması)
    file_size = path.stat().st_size
    if file_size > max_file_size_bytes:
        raise ModelSecurityError(
            f"Model dosyası boyutu izin verilen sınırı aşıyor: "
            f"{file_size} > {max_file_size_bytes} bayt."
        )

    # PyTorch .pt/.pth dosyası ise ZIP / pickle opcode taraması yap
    if suffix in (".pt", ".pth", ".bin"):
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path, "r") as zf:
                    for zinfo in zf.infolist():
                        if ".." in zinfo.filename or zinfo.filename.startswith("/"):
                            msg = (
                                "Zip tabanlı modelde path traversal tespit edildi: "
                                f"{zinfo.filename}"
                            )
                            raise ModelSecurityError(msg)
                        if zinfo.filename.endswith(".pkl"):
                            pkl_bytes = zf.read(zinfo.filename)
                            inspect_pickle_stream_safety(pkl_bytes)
            else:
                with path.open("rb") as f:
                    inspect_pickle_stream_safety(f.read())
        except ModelSecurityError:
            raise
        except Exception:
            # Okunamayan veya şifrelenmiş zip durumları weights_only=True tarafından yakalanır
            pass


def safe_load_weights(
    file_path: Path | str,
    expected_sha256: str | None = None,
    verify_integrity: bool = True,
    map_location: str | torch.device = "cpu",
    max_file_size_bytes: int = 1024 * 1024 * 1024,
) -> dict[str, torch.Tensor]:
    """Güvenlik denetimleri, hash doğrulaması ve güvenli format garantisiyle ağırlıkları yükler.

    Kurallar:
    1. Zararlı dosya uzantıları (.pkl, .pickle vb.) doğrudan reddedilir.
    2. Beklenen SHA-256 özeti verilmişse uyuşmazlıkta ModelIntegrityError fırlatılır.
    3. Safetensors formatı sıfır pickle ile yüklenir.
    4. PyTorch formatı kesinlikle weights_only=True ile yüklenir (weights_only=False yasaktır).
    """
    path = Path(file_path).resolve()

    # 1. Güvenlik ve boyut denetimi
    inspect_weights_file_security(path, max_file_size_bytes=max_file_size_bytes)

    # 2. SHA-256 Kriptografik Bütünlük Doğrulaması
    if verify_integrity and expected_sha256:
        verify_file_sha256(path, expected_sha256)

    # 3. Yükleme (Format ayrımı)
    suffix = path.suffix.lower()
    if suffix == ".safetensors":
        return load_safetensors(path, device=map_location)

    # PyTorch .pt / .pth yükleme (weights_only=True zorunlu)
    try:
        loaded = torch.load(
            path,
            map_location=map_location,
            weights_only=True,
        )
    except Exception as exc:
        raise ModelSecurityError(
            f"PyTorch ağırlık dosyası güvenli (weights_only=True) yüklenemedi: {exc}"
        ) from exc

    if isinstance(loaded, dict) and "state_dict" in loaded:
        state_dict = loaded["state_dict"]
    elif isinstance(loaded, dict) and "model_state_dict" in loaded:
        state_dict = loaded["model_state_dict"]
    elif isinstance(loaded, dict):
        state_dict = loaded
    else:
        raise ModelSecurityError(
            f"Yüklenen nesne geçerli bir state_dict sözlüğü değil: {type(loaded)}"
        )

    # State dict elemanlarının tensör olduğunu doğrula
    return {str(k): v for k, v in state_dict.items() if isinstance(v, torch.Tensor)}


def safe_save_weights(
    model_or_state_dict: nn.Module | Mapping[str, torch.Tensor],
    file_path: Path | str,
    weights_format: Literal["safetensors", "pytorch"] = "safetensors",
    metadata: Mapping[str, str] | None = None,
) -> tuple[Path, str]:
    """Ağırlıkları belirtilen güvenli formatta kaydeder ve SHA-256 hash'ini döner."""
    path = Path(file_path)
    state_dict = (
        model_or_state_dict.state_dict()
        if isinstance(model_or_state_dict, nn.Module)
        else dict(model_or_state_dict)
    )

    if weights_format == "safetensors" or path.suffix.lower() == ".safetensors":
        if not path.name.endswith(".safetensors"):
            path = path.with_suffix(".safetensors")
        sha256_hash = save_safetensors(state_dict, path, metadata=metadata)
        return path, sha256_hash

    # PyTorch .pt formatı
    if path.suffix.lower() not in (".pt", ".pth"):
        path = path.with_suffix(".pt")

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state_dict, path)
    sha256_hash = compute_file_sha256(path)
    return path, sha256_hash


__all__ = [
    "DISALLOWED_PICKLE_MODULES",
    "FORBIDDEN_EXTENSIONS",
    "ModelIntegrityError",
    "ModelRegistryError",
    "ModelSecurityError",
    "SAFE_WEIGHTS_EXTENSIONS",
    "SAFETENSORS_TO_TORCH_DTYPE",
    "TORCH_TO_SAFETENSORS_DTYPE",
    "compute_file_sha256",
    "inspect_pickle_stream_safety",
    "inspect_weights_file_security",
    "load_safetensors",
    "safe_load_weights",
    "safe_save_weights",
    "save_safetensors",
    "validate_safe_filename",
    "verify_file_sha256",
]
