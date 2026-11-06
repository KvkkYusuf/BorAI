"""Zaman serisi ve borsa verileri için PyTorch başlangıç model mimarileri.

Kural gereği karmaşık LSTM/Transformer modellerine geçmeden önce
hafif, CPU-dostu ve parametre sayısı kontrollü küçük MLP (SmallMLP)
ve 1D CNN (SmallCNN1D) mimarileri kullanılır.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import torch
import torch.nn as nn


class ModelArchitectureType(StrEnum):
    """Desteklenen model mimari türleri."""

    SMALL_MLP = "small_mlp"
    SMALL_CNN_1D = "small_cnn_1d"
    LSTM = "lstm"
    GRU = "gru"
    TRANSFORMER = "transformer"


@dataclass(frozen=True)
class MLPConfig:
    """Küçük MLP mimarisi yapılandırması."""

    input_dim: int
    hidden_dims: tuple[int, ...] = (64, 32)
    output_dim: int = 1
    dropout: float = 0.1
    use_batch_norm: bool = True

    def __post_init__(self) -> None:
        if self.input_dim <= 0:
            raise ValueError(f"input_dim pozitif olmalı, verilen: {self.input_dim}")
        if self.output_dim <= 0:
            raise ValueError(f"output_dim pozitif olmalı, verilen: {self.output_dim}")
        if not (0.0 <= self.dropout < 1.0):
            raise ValueError(f"dropout [0, 1) aralığında olmalı, verilen: {self.dropout}")
        if not self.hidden_dims:
            raise ValueError("En az bir gizli katman (hidden_dims) belirtilmelidir.")


@dataclass(frozen=True)
class CNN1DConfig:
    """Küçük 1D CNN mimarisi yapılandırması."""

    in_channels: int
    seq_len: int
    num_filters: tuple[int, ...] = (32, 64)
    kernel_size: int = 3
    output_dim: int = 1
    dropout: float = 0.1

    def __post_init__(self) -> None:
        if self.in_channels <= 0:
            raise ValueError(f"in_channels pozitif olmalı, verilen: {self.in_channels}")
        if self.seq_len <= 0:
            raise ValueError(f"seq_len pozitif olmalı, verilen: {self.seq_len}")
        if self.kernel_size <= 0:
            raise ValueError(f"kernel_size pozitif olmalı, verilen: {self.kernel_size}")
        if not (0.0 <= self.dropout < 1.0):
            raise ValueError(f"dropout [0, 1) aralığında olmalı, verilen: {self.dropout}")
        if not self.num_filters:
            raise ValueError("En az bir filtre katmanı (num_filters) belirtilmelidir.")


@dataclass(frozen=True)
class ArchitectureJustification:
    """Mimari seçim gerekçelendirmesi ve kısıt değerlendirmesi."""

    recommended_type: ModelArchitectureType
    sample_count: int
    sequence_length: int
    feature_count: int
    is_complex_model_justified: bool
    rationale: str
    reasons: list[str] = field(default_factory=list)


def count_parameters(model: nn.Module) -> int:
    """Modelin toplam eğitilebilir parametre sayısını döndürür."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


class SmallMLP(nn.Module):
    """Zaman serisi özet vektörleri ve tabüler özellikler için hafif MLP modeli."""

    def __init__(self, config: MLPConfig) -> None:
        super().__init__()
        self.config = config

        layers: list[nn.Module] = []
        prev_dim = config.input_dim

        for hidden_dim in config.hidden_dims:
            layers.append(nn.Linear(prev_dim, hidden_dim))
            if config.use_batch_norm:
                layers.append(nn.BatchNorm1d(hidden_dim))
            layers.append(nn.ReLU())
            if config.dropout > 0.0:
                layers.append(nn.Dropout(p=config.dropout))
            prev_dim = hidden_dim

        layers.append(nn.Linear(prev_dim, config.output_dim))
        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Girdi tensörünü modelden geçirir.

        Args:
            x: Girdi tensörü. Şekli (batch_size, input_dim) veya
               (batch_size, seq_len, num_features) olabilir;
               2'den fazla boyut varsa otomatik düzleştirilir.
        """
        if x.dim() > 2:
            x = x.reshape(x.size(0), -1)
        if x.size(-1) != self.config.input_dim:
            raise ValueError(
                f"Girdi boyutu ({x.size(-1)}) ile model giriş boyutu "
                f"({self.config.input_dim}) eşleşmiyor."
            )
        out: torch.Tensor = self.network(x)
        return out


class SmallCNN1D(nn.Module):
    """Zamansal sıralı pencereler için hafif 1 Boyutlu Konvolüsyonel Ağ."""

    def __init__(self, config: CNN1DConfig) -> None:
        super().__init__()
        self.config = config

        conv_layers: list[nn.Module] = []
        in_c = config.in_channels

        for out_c in config.num_filters:
            # Lookahead bias sızıntısını önlemek ve zamansal boyutu korumak için
            # padding = (kernel_size - 1) // 2 veya causal dolgu mantığı
            padding = (config.kernel_size - 1) // 2
            conv_layers.append(
                nn.Conv1d(
                    in_channels=in_c,
                    out_channels=out_c,
                    kernel_size=config.kernel_size,
                    padding=padding,
                )
            )
            conv_layers.append(nn.BatchNorm1d(out_c))
            conv_layers.append(nn.ReLU())
            if config.dropout > 0.0:
                conv_layers.append(nn.Dropout(p=config.dropout))
            in_c = out_c

        self.conv_net = nn.Sequential(*conv_layers)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.head = nn.Linear(in_c, config.output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """1D CNN ileri geçişi.

        Args:
            x: (batch_size, in_channels, seq_len) veya
               (batch_size, seq_len, in_channels).
               Eğer (batch_size, seq_len, in_channels) verilirse otomatik olarak
               (batch_size, in_channels, seq_len) şekline dönüştürülür.
        """
        if x.dim() != 3:
            raise ValueError(f"1D CNN için girdi 3 boyutlu olmalıdır, verilen boyut: {x.dim()}")

        # Boyut kontrolü ve permütasyon (B, L, C) -> (B, C, L)
        if x.size(1) == self.config.seq_len and x.size(2) == self.config.in_channels:
            x = x.transpose(1, 2)
        elif x.size(1) != self.config.in_channels or x.size(2) != self.config.seq_len:
            raise ValueError(
                f"Girdi şekli ({tuple(x.shape)}) konfigürasyon "
                f"(in_channels={self.config.in_channels}, seq_len={self.config.seq_len}) "
                "ile uyuşmuyor."
            )

        feat = self.conv_net(x)
        pooled = self.pool(feat).squeeze(-1)
        out: torch.Tensor = self.head(pooled)
        return out


def evaluate_architecture_justification(
    sample_count: int,
    sequence_length: int,
    feature_count: int,
    baseline_beaten: bool = True,
) -> ArchitectureJustification:
    """Veri hacmi ve deney gereksinimlerine göre model karmaşıklığını değerlendirir.

    Kural: Küçük veri ve deney gerekçelendirilmeyen durumlarda LSTM/Transformer gibi
    ağır mimarilere izin verilmez; küçük MLP veya 1D CNN önerilir.
    """
    reasons: list[str] = []

    if not baseline_beaten:
        reasons.append("Naif baseline henüz geçilemedi; karmaşık modeller değerlendirilemez.")
        return ArchitectureJustification(
            recommended_type=ModelArchitectureType.SMALL_MLP,
            sample_count=sample_count,
            sequence_length=sequence_length,
            feature_count=feature_count,
            is_complex_model_justified=False,
            rationale="Baseline geçilmediği için en sade başlangıç modeli (SmallMLP) şarttır.",
            reasons=reasons,
        )

    # Veri büyüklüğü kriteri (en az 50.000 gözlem ve yeterli dizi uzunluğu)
    is_data_sufficient = sample_count >= 50000 and sequence_length >= 30

    if sequence_length > 1 and sequence_length <= 30:
        recommended = ModelArchitectureType.SMALL_CNN_1D
        rationale = (
            "Kısa zaman serisi penceresi için 1D CNN hızlı, düşük parametreli ve "
            "CPU dostu yerel örüntü yakalamaktadır."
        )
        reasons.append("Pencere boyutu 1-30 bar arasında; 1D CNN uygundur.")
    elif sequence_length > 30 and is_data_sufficient:
        recommended = ModelArchitectureType.LSTM
        rationale = (
            "Yeterli örneklem sayısı (>50k) ve uzun pencere (>30 bar) nedeniyle "
            "reküran (LSTM/GRU) mimari değerlendirilebilir."
        )
        reasons.append("Büyük veri hacmi ve uzun zamansal bağımlılık mevcut.")
        return ArchitectureJustification(
            recommended_type=recommended,
            sample_count=sample_count,
            sequence_length=sequence_length,
            feature_count=feature_count,
            is_complex_model_justified=True,
            rationale=rationale,
            reasons=reasons,
        )
    else:
        recommended = ModelArchitectureType.SMALL_MLP
        rationale = (
            "Örneklem sayısı veya dizi uzunluğu sınırlı olduğundan overfitting "
            "riskine karşı sade MLP mimarisi önerilmektedir."
        )
        reasons.append("Veri büyüklüğü küçük veya tabüler özet özellikler kullanılmakta.")

    return ArchitectureJustification(
        recommended_type=recommended,
        sample_count=sample_count,
        sequence_length=sequence_length,
        feature_count=feature_count,
        is_complex_model_justified=False,
        rationale=rationale,
        reasons=reasons,
    )


def create_model(
    arch_type: ModelArchitectureType | str,
    config: MLPConfig | CNN1DConfig | dict[str, Any],
) -> nn.Module:
    """Belirtilen mimari türü ve konfigürasyona göre PyTorch modelini oluşturur."""
    normalized_type = ModelArchitectureType(arch_type) if isinstance(arch_type, str) else arch_type

    if normalized_type == ModelArchitectureType.SMALL_MLP:
        if isinstance(config, dict):
            mlp_cfg = MLPConfig(**config)
        elif isinstance(config, MLPConfig):
            mlp_cfg = config
        else:
            raise TypeError("SmallMLP için MLPConfig veya dict beklenmektedir.")
        return SmallMLP(mlp_cfg)

    elif normalized_type == ModelArchitectureType.SMALL_CNN_1D:
        if isinstance(config, dict):
            cnn_cfg = CNN1DConfig(**config)
        elif isinstance(config, CNN1DConfig):
            cnn_cfg = config
        else:
            raise TypeError("SmallCNN1D için CNN1DConfig veya dict beklenmektedir.")
        return SmallCNN1D(cnn_cfg)

    else:
        raise NotImplementedError(
            f"{normalized_type} henüz aktif edilmedi; veri ve deney gerekçelendirmesi "
            "sağlanana kadar SmallMLP veya SmallCNN1D kullanılmalıdır."
        )
