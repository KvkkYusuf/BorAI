"""User interface router serving the responsive web prototype and metadata."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["UI"])

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


@router.get(
    "/",
    response_class=FileResponse,
    summary="Kullanıcı Arayüzü Web Prototipi",
    description="Masaüstü ve mobil uyumlu Borsa Analiz Sistemi arayüz prototipini sunar.",
)
async def get_index() -> FileResponse:
    """Serve the root responsive web prototype."""
    return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")


@router.get(
    "/app",
    response_class=FileResponse,
    summary="Kullanıcı Arayüzü Uygulama Sayfası",
    description="Pano, hisse filtreleme, detay ve teknik analiz ekranlarını içeren web arayüzü.",
)
async def get_app_view() -> FileResponse:
    """Serve the responsive web application shell."""
    return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")
