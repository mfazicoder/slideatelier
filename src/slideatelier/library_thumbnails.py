"""Generate PNG thumbnails for each asset in the library.

Pipeline: .pptx → LibreOffice headless → .pdf → pdf2image (poppler) → .png per slide.

Stored at: library/thumbnails/<asset-id-flattened>.png

Filenames flatten the slash-separated asset ID with double underscores:
  asset_id "business-diagrams-powerpoint/Business-Diagrams-Infographic-01/3"
  → "business-diagrams-powerpoint__Business-Diagrams-Infographic-01__3.png"

LibreOffice conversion is slow (~3-5s per file). Run once after `library scan`;
re-run only after adding new files. We convert each .pptx once (yielding all
its slides as one PDF), then split into PNGs — much faster than per-slide.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from .library import LibraryAsset, LibraryCatalog


def _flat_filename(asset_id: str) -> str:
    return asset_id.replace("/", "__") + ".png"


def asset_thumbnail_path(thumbnails_dir: Path, asset_id: str) -> Path:
    return thumbnails_dir / _flat_filename(asset_id)


def _convert_pptx_to_pdf(pptx: Path, output_dir: Path) -> Path | None:
    """Use LibreOffice headless to convert .pptx → .pdf. Returns path to the produced PDF or None on failure."""
    soffice = shutil.which("soffice")
    if soffice is None:
        raise RuntimeError(
            "LibreOffice 'soffice' not found in PATH. Install via: brew install --cask libreoffice"
        )
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(output_dir),
        str(pptx),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return None
    pdf_path = output_dir / (pptx.stem + ".pdf")
    return pdf_path if pdf_path.exists() else None


def _split_pdf_to_pngs(pdf_path: Path, dpi: int = 100) -> list:
    """Split a multi-page PDF into a list of PIL.Image objects (one per page)."""
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        raise RuntimeError("pdf2image not installed. Run: uv add pdf2image") from e
    return convert_from_path(str(pdf_path), dpi=dpi)


def generate_thumbnails(
    catalog: LibraryCatalog,
    thumbnails_dir: Path,
    *,
    dpi: int = 100,
    max_width_px: int = 800,
    overwrite: bool = False,
    progress_callback=None,
) -> dict:
    """Generate one PNG per asset in the catalog. Skips assets that already
    have a thumbnail unless overwrite=True. Returns counts dict."""
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    # Group assets by source file — we convert each .pptx once.
    by_file: dict[str, list[LibraryAsset]] = {}
    for asset in catalog.assets:
        by_file.setdefault(asset.file_path, []).append(asset)

    counts = {"converted": 0, "skipped": 0, "failed": 0, "files_processed": 0}
    for file_path_str, assets in by_file.items():
        # Skip if all thumbnails already exist
        if not overwrite:
            all_present = all(
                asset_thumbnail_path(thumbnails_dir, a.id).exists() for a in assets
            )
            if all_present:
                counts["skipped"] += len(assets)
                continue

        if progress_callback:
            progress_callback(f"Processing {Path(file_path_str).name} ({len(assets)} slides)…")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pdf = _convert_pptx_to_pdf(Path(file_path_str), tmp)
            if pdf is None:
                counts["failed"] += len(assets)
                if progress_callback:
                    progress_callback(f"  ! LibreOffice conversion failed for {file_path_str}")
                continue

            try:
                pages = _split_pdf_to_pngs(pdf, dpi=dpi)
            except Exception as e:  # noqa: BLE001
                counts["failed"] += len(assets)
                if progress_callback:
                    progress_callback(f"  ! PDF→PNG conversion failed: {e}")
                continue

            counts["files_processed"] += 1

            for asset in assets:
                idx = asset.slide_index - 1  # 1-based → 0-based
                if idx >= len(pages):
                    counts["failed"] += 1
                    continue
                img = pages[idx]
                # Downscale if too wide
                if img.width > max_width_px:
                    ratio = max_width_px / img.width
                    new_size = (max_width_px, int(img.height * ratio))
                    img = img.resize(new_size)
                out = asset_thumbnail_path(thumbnails_dir, asset.id)
                img.save(out, "PNG", optimize=True)
                counts["converted"] += 1

    return counts


def update_catalog_with_thumbnails(catalog: LibraryCatalog, thumbnails_dir: Path) -> int:
    """Walk the catalog and set has_thumbnail=True where the file exists."""
    updated = 0
    for asset in catalog.assets:
        path = asset_thumbnail_path(thumbnails_dir, asset.id)
        was = asset.has_thumbnail
        asset.has_thumbnail = path.exists()
        if asset.has_thumbnail != was:
            updated += 1
    return updated
