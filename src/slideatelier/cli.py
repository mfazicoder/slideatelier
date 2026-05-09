import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from .cache import DeckCache
from .claude_client import make_client
from .config import Config
from .ingestion import extract_design_system, ingest_to_template
from .library import load_catalog, save_catalog, scan_library
from .metadata import GeneratedDeck
from .planner import plan_deck
from .renderer import DeckRenderer
from .research import do_research, dossier_to_storyboard_context
from .storyboard import Storyboard, expand_storyboard_to_deck, plan_storyboard
from .template import (
    Template,
    load_default_template,
    load_template,
    save_template,
)
from .template_creator import create_template_interactive
from .template_inspector import detect_layout_map, inspect_master

app = typer.Typer(help="slideAtelier — consulting-quality presentation generator")
template_app = typer.Typer(help="Manage presentation templates")
cache_app = typer.Typer(help="Inspect and manage the response cache")
library_app = typer.Typer(help="Manage the visual asset library")
invite_app = typer.Typer(help="Manage invite codes for the private-beta signup gate")
app.add_typer(template_app, name="template")
app.add_typer(cache_app, name="cache")
app.add_typer(library_app, name="library")
app.add_typer(invite_app, name="invite")
console = Console()


# Default categories to scan — the curated set.
DEFAULT_LIBRARY_CATEGORIES = [
    "Business Diagrams",
    "Funnels",
    "Pyramid",
    "Venn",
    "Timeline",
    "Comparison Charts",
    "Roadmap",
    "Cycle",
    "Marketing",
    "Real Estate",
    "SCRUM",
    "Watercolor",
    "Alphabet",
]


def _library_catalog_path(config: Config) -> Path:
    return Path("./library/catalog.json")


def _resolve_template(name_or_path: str | Path | None, config: Config) -> Template:
    """Accept '--template default', '--template default.json', or '--template /path/to/file.json'."""
    if name_or_path is None:
        return load_default_template(config.templates_dir)
    s = str(name_or_path)
    p = Path(s)
    if p.exists():
        return load_template(p)
    # Try as a name in the templates dir
    candidate = config.templates_dir / s
    if candidate.exists():
        return load_template(candidate)
    candidate_json = config.templates_dir / f"{s}.json"
    if candidate_json.exists():
        return load_template(candidate_json)
    raise FileNotFoundError(f"Template not found: {s}")


@app.command()
def generate(
    content: Path = typer.Option(..., "--content", "-c", help="Path to content brief (markdown or text)"),
    out: Path = typer.Option(Path("output/deck.pptx"), "--out", "-o", help="Output .pptx path"),
    template: str | None = typer.Option(
        None, "--template", "-t",
        help="Template name (e.g. 'acme') or path to JSON. Defaults to templates/default.json.",
    ),
    requirements: str = typer.Option("", "--requirements", "-r", help="Additional requirements"),
    spec_out: Path | None = typer.Option(None, "--spec-out", help="Save the spec (deck + generation metadata) as JSON"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Bypass the response cache and force a fresh generation"),
):
    """Generate a presentation from a content brief."""
    if not content.exists():
        console.print(f"[red]Content file not found:[/red] {content}")
        raise typer.Exit(1)

    config = Config.from_env()
    config.require_api_key()
    client = make_client(config)
    tpl = _resolve_template(template, config)

    mode = "master" if tpl.master_pptx else "json"
    console.print(f"[dim]Template: {tpl.name} ({mode} mode)[/dim]")

    console.print(f"[cyan]Reading[/cyan] {content}")
    content_text = content.read_text()

    if regenerate:
        console.print("[yellow]--regenerate set: bypassing cache[/yellow]")

    console.print(f"[cyan]Planning slides[/cyan] with {config.model}…")
    deck, metadata = plan_deck(client, config, content_text, requirements, regenerate=regenerate)
    cache_label = "[dim](cache hit)[/dim]" if metadata.cache_hit else f"[dim]({metadata.duration_seconds:.1f}s)[/dim]"
    console.print(f"[green]✓[/green] {len(deck.slides)} slides: {deck.title!r} {cache_label}")

    if spec_out:
        spec_out.parent.mkdir(parents=True, exist_ok=True)
        wrapped = GeneratedDeck(metadata=metadata, deck=deck)
        spec_out.write_text(wrapped.model_dump_json(indent=2))
        console.print(f"[green]✓[/green] Spec → {spec_out}")

    console.print(f"[cyan]Rendering[/cyan] → {out}")
    DeckRenderer(tpl, config.templates_dir).render(deck, out)
    console.print(f"[green]✓[/green] Deck saved to {out}")


@template_app.command("create")
def template_create(
    name: str | None = typer.Option(None, "--name", "-n", help="Template name"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output JSON path"),
):
    """Create a new JSON-mode template via interactive Q&A (no master file)."""
    config = Config.from_env()
    if out is None:
        slug = (name or "custom").lower().replace(" ", "-")
        out = config.templates_dir / f"{slug}.json"
    if out.exists():
        if not Confirm.ask(f"[yellow]{out} exists. Overwrite?[/yellow]", default=False):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)
    create_template_interactive(out)


@template_app.command("register")
def template_register(
    master_pptx: Path = typer.Argument(..., help="Path to master .pptx file"),
    name: str = typer.Option(..., "--name", "-n", help="Template name (used as filename)"),
    description: str = typer.Option("", "--description", "-d", help="One-line description"),
    no_copy: bool = typer.Option(
        False, "--no-copy",
        help="Don't copy the master into templates/. Reference the original path instead.",
    ),
):
    """Register a master .pptx file as a template (master mode — preferred).

    The renderer will add generated slides into the master, inheriting all of
    its theme, colors, fonts, logos, and footers."""
    config = Config.from_env()

    if not master_pptx.exists():
        console.print(f"[red]Not found:[/red] {master_pptx}")
        raise typer.Exit(1)
    if master_pptx.suffix.lower() != ".pptx":
        console.print(f"[red]Not a .pptx file:[/red] {master_pptx}")
        raise typer.Exit(1)

    config.templates_dir.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")

    # Decide where the master lives
    if no_copy:
        master_ref = str(master_pptx.resolve())
    else:
        dest = config.templates_dir / f"{slug}-master.pptx"
        shutil.copy2(master_pptx, dest)
        master_ref = dest.name  # relative to templates dir

    # Detect layout mapping
    console.print(f"[cyan]Inspecting[/cyan] {master_pptx}…")
    layout_map = detect_layout_map(master_pptx)

    tpl = Template(
        name=name,
        description=description,
        master_pptx=master_ref,
        layout_map=layout_map,
    )
    out_json = config.templates_dir / f"{slug}.json"
    save_template(tpl, out_json)

    console.print(f"\n[green]✓[/green] Registered template [bold]{name}[/bold]")
    console.print(f"  JSON: [cyan]{out_json}[/cyan]")
    console.print(f"  Master: [cyan]{master_ref}[/cyan]")
    console.print("\n[bold]Detected layout map:[/bold]")
    for our_name, idx in layout_map.items():
        console.print(f"  {our_name:18s} → layout {idx}")
    console.print(f"\n[dim]Use it: [bold]uv run atelier generate -c brief.md -t {slug} -o output/deck.pptx[/bold][/dim]")
    console.print(
        f"[dim]Inspect the master: [bold]uv run atelier template inspect {master_pptx}[/bold][/dim]"
    )


@template_app.command("inspect")
def template_inspect(
    master_pptx: Path = typer.Argument(..., help="Path to .pptx file"),
):
    """List the layouts and placeholders inside a .pptx file."""
    inspect_master(master_pptx)


@template_app.command("list")
def template_list():
    """List available templates."""
    config = Config.from_env()
    if not config.templates_dir.exists():
        console.print("[dim]No templates directory yet[/dim]")
        return
    templates = sorted(config.templates_dir.glob("*.json"))
    if not templates:
        console.print("[dim]No templates yet[/dim]")
        return
    for t in templates:
        try:
            tpl = load_template(t)
            mode = "[cyan]master[/cyan]" if tpl.master_pptx else "[yellow]json[/yellow]"
            marker = " [dim](default)[/dim]" if t.name == "default.json" else ""
            console.print(f"  • [bold]{t.stem}[/bold]{marker} ({mode}) — {tpl.description or tpl.name}")
        except Exception as e:  # noqa: BLE001
            console.print(f"  • {t.name} — [red]invalid: {e}[/red]")


@template_app.command("show")
def template_show(
    name: str = typer.Argument(..., help="Template name or path"),
):
    """Print a template's contents."""
    config = Config.from_env()
    tpl = _resolve_template(name, config)
    console.print_json(data=tpl.model_dump())


@app.command()
def storyboard(
    content: Path = typer.Option(..., "--content", "-c", help="Path to content brief"),
    out: Path = typer.Option(Path("output/storyboard.json"), "--out", "-o", help="Output storyboard JSON"),
    requirements: str = typer.Option("", "--requirements", "-r", help="Additional requirements"),
):
    """Stage 1: brief → storyboard. The argument structure only (no body content).

    The storyboard is editable; pass it to `atelier expand` to flesh out into a full deck."""
    if not content.exists():
        console.print(f"[red]Content file not found:[/red] {content}")
        raise typer.Exit(1)

    config = Config.from_env()
    config.require_api_key()
    client = make_client(config)

    console.print(f"[cyan]Reading[/cyan] {content}")
    content_text = content.read_text()

    console.print(f"[cyan]Sketching storyboard[/cyan] with {config.model}…")
    sb, metadata = plan_storyboard(client, config, content_text, requirements)
    console.print(
        f"[green]✓[/green] {len(sb.slides)} slides "
        f"[dim]({metadata.duration_seconds:.1f}s)[/dim]"
    )
    console.print(f"  [bold]title:[/bold] {sb.title}")
    console.print(f"  [bold]core_message:[/bold] {sb.core_message}")
    console.print()
    for i, s in enumerate(sb.slides):
        console.print(f"  [cyan]{i:2}[/cyan] [{s.layout:16}] {s.title}")
        console.print(f"        [dim]{s.purpose}[/dim]")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sb.model_dump_json(indent=2))
    console.print(f"\n[green]✓[/green] Storyboard saved to [cyan]{out}[/cyan]")
    console.print(
        f"[dim]Edit it, then expand: [bold]atelier expand --storyboard {out} --content {content}[/bold][/dim]"
    )


@app.command()
def expand(
    storyboard_path: Path = typer.Option(..., "--storyboard", "-s", help="Path to storyboard JSON"),
    content: Path = typer.Option(..., "--content", "-c", help="Path to original brief"),
    out: Path = typer.Option(Path("output/deck.pptx"), "--out", "-o", help="Output .pptx path"),
    template: str | None = typer.Option(None, "--template", "-t", help="Template name or path"),
    requirements: str = typer.Option("", "--requirements", "-r", help="Additional requirements"),
    spec_out: Path | None = typer.Option(None, "--spec-out", help="Save the full deck spec as JSON"),
):
    """Stage 2: storyboard + brief → full deck (with body content) → render to .pptx.

    Preserves the storyboard's structure (titles, layouts, core_message, arc)
    and fills in body bullets, columns, speaker notes."""
    if not storyboard_path.exists():
        console.print(f"[red]Storyboard not found:[/red] {storyboard_path}")
        raise typer.Exit(1)
    if not content.exists():
        console.print(f"[red]Brief not found:[/red] {content}")
        raise typer.Exit(1)

    config = Config.from_env()
    config.require_api_key()
    client = make_client(config)
    tpl = _resolve_template(template, config)

    console.print(f"[cyan]Loading[/cyan] storyboard from {storyboard_path}")
    sb = Storyboard.model_validate_json(storyboard_path.read_text())
    content_text = content.read_text()

    console.print(f"[cyan]Expanding[/cyan] storyboard ({len(sb.slides)} slides) into full deck…")
    deck, metadata = expand_storyboard_to_deck(client, config, sb, content_text, requirements)
    console.print(f"[green]✓[/green] Full deck ready [dim]({metadata.duration_seconds:.1f}s)[/dim]")

    if spec_out:
        wrapped = GeneratedDeck(metadata=metadata, deck=deck)
        spec_out.parent.mkdir(parents=True, exist_ok=True)
        spec_out.write_text(wrapped.model_dump_json(indent=2))
        console.print(f"[green]✓[/green] Spec → {spec_out}")

    console.print(f"[cyan]Rendering[/cyan] → {out}")
    DeckRenderer(tpl, config.templates_dir).render(deck, out)
    console.print(f"[green]✓[/green] Deck saved to {out}")


@cache_app.command("list")
def cache_list():
    """List cached generations (newest first)."""
    config = Config.from_env()
    cache = DeckCache(config.cache_dir, enabled=True)
    entries = cache.list_entries()
    if not entries:
        console.print("[dim]Cache is empty.[/dim]")
        return

    table = Table()
    table.add_column("Hash", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Slides", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Modified")
    for e in entries:
        table.add_row(
            e["key"][:12] + "…",
            e["title"],
            str(e["slide_count"]),
            f"{e['size_bytes']:,} B",
            e["modified_at"].strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@cache_app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove all cached generations."""
    config = Config.from_env()
    cache = DeckCache(config.cache_dir, enabled=True)
    entries = cache.list_entries()
    if not entries:
        console.print("[dim]Cache is already empty.[/dim]")
        return
    if not yes and not Confirm.ask(f"[yellow]Delete {len(entries)} cache entries?[/yellow]", default=False):
        console.print("[dim]Aborted.[/dim]")
        return
    n = cache.clear()
    console.print(f"[green]✓[/green] Cleared {n} cache entries.")


@app.command("render-spec")
def render_spec(
    spec: Path = typer.Argument(..., help="Path to a deck spec JSON (wrapped GeneratedDeck or bare SlideDeck)"),
    out: Path = typer.Option(Path("output/deck.pptx"), "--out", "-o", help="Output .pptx path"),
    template: str | None = typer.Option(
        None, "--template", "-t",
        help="Template name (e.g. 'default') or path to JSON.",
    ),
):
    """Render a deck from an existing spec JSON — bypasses the LLM.

    Useful for: (a) testing changes to the renderer without re-paying for API calls,
    (b) hand-editing a spec to inject asset_refs from the library, (c) reproducing
    an old deck after a renderer fix."""
    if not spec.exists():
        console.print(f"[red]Spec file not found:[/red] {spec}")
        raise typer.Exit(1)

    import json
    raw = json.loads(spec.read_text())
    # Accept either wrapped (GeneratedDeck) or bare (SlideDeck) form
    deck_dict = raw.get("deck", raw)

    from .models import SlideDeck
    deck = SlideDeck.model_validate(deck_dict)

    config = Config.from_env()
    tpl = _resolve_template(template, config)
    mode = "master" if tpl.master_pptx else "json"
    asset_count = sum(1 for s in deck.slides if s.asset_ref)
    console.print(f"[dim]Template: {tpl.name} ({mode} mode)[/dim]")
    console.print(f"[dim]Spec: {len(deck.slides)} slides, {asset_count} with asset_refs[/dim]")
    console.print(f"[cyan]Rendering[/cyan] → {out}")
    DeckRenderer(tpl, config.templates_dir).render(deck, out)
    console.print(f"[green]✓[/green] Deck saved to {out}")


@library_app.command("scan")
def library_scan(
    root: Path = typer.Option(
        Path("/Users/farha/Documents/Powerpoint slides/presenter-kit-powerpoint 2"),
        "--root", "-r",
        help="Root directory containing category folders of .pptx files",
    ),
    categories: list[str] | None = typer.Option(
        None, "--category", "-c",
        help="Substring filter for category folder names. Repeat for multiple. "
             "Default: scans the curated 13 categories.",
    ),
    out: Path = typer.Option(Path("library/catalog.json"), "--out", "-o", help="Output catalog JSON"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress per-file progress"),
):
    """Scan a library directory and build a JSON catalog of every slide as an addressable asset."""
    if not root.exists():
        console.print(f"[red]Library root not found:[/red] {root}")
        raise typer.Exit(1)

    cats = categories if categories else DEFAULT_LIBRARY_CATEGORIES
    console.print(f"[cyan]Scanning[/cyan] {root}")
    console.print(f"[dim]Categories: {', '.join(cats)}[/dim]")

    def progress(msg: str):
        if not quiet:
            console.print(f"  · {msg}")

    catalog = scan_library(root, categories=cats, progress_callback=progress)
    save_catalog(catalog, out)

    console.print(f"\n[green]✓[/green] Indexed [bold]{catalog.asset_count}[/bold] assets across "
                  f"[bold]{len(catalog.categories)}[/bold] categories")
    console.print(f"  Catalog saved to [cyan]{out}[/cyan]")


@library_app.command("categories")
def library_categories(
    catalog_path: Path = typer.Option(Path("library/catalog.json"), "--catalog", help="Path to catalog JSON"),
):
    """List categories in the catalog with asset counts."""
    catalog = load_catalog(catalog_path)
    by_cat = catalog.by_category()
    table = Table()
    table.add_column("Category", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("Slides (assets)", justify="right")
    for cat in sorted(by_cat.keys()):
        assets = by_cat[cat]
        files = {a.file_stem for a in assets}
        table.add_row(cat, str(len(files)), str(len(assets)))
    table.add_row("[bold]Total[/bold]", "", f"[bold]{catalog.asset_count}[/bold]")
    console.print(table)


@library_app.command("list")
def library_list(
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category (substring match)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows to display"),
    catalog_path: Path = typer.Option(Path("library/catalog.json"), "--catalog", help="Path to catalog JSON"),
):
    """List assets in the catalog, optionally filtered by category."""
    catalog = load_catalog(catalog_path)
    assets = catalog.assets
    if category:
        assets = [a for a in assets if category.lower() in a.category.lower()]
    table = Table()
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Category")
    table.add_column("Shapes", justify="right")
    table.add_column("Sample text", overflow="fold")
    for a in assets[:limit]:
        sample = " · ".join(a.text_snippets[:2])
        table.add_row(a.id, a.category, str(a.shape_count), sample)
    console.print(table)
    if len(assets) > limit:
        console.print(f"[dim]…showing {limit} of {len(assets)} matches.[/dim]")


@library_app.command("inspect")
def library_inspect(
    asset_id: str = typer.Argument(..., help="Asset ID from catalog (e.g. 'business-diagrams-powerpoint/Business-Diagrams-Infographic-01/3')"),
    catalog_path: Path = typer.Option(Path("library/catalog.json"), "--catalog", help="Path to catalog JSON"),
):
    """Print the full metadata + text snippets for one asset."""
    catalog = load_catalog(catalog_path)
    asset = catalog.find(asset_id)
    if asset is None:
        console.print(f"[red]Not found:[/red] {asset_id}")
        raise typer.Exit(1)
    console.print_json(data=asset.model_dump())


@library_app.command("thumbnails")
def library_thumbnails(
    catalog_path: Path = typer.Option(Path("library/catalog.json"), "--catalog", help="Path to catalog JSON"),
    thumbnails_dir: Path = typer.Option(Path("library/thumbnails"), "--out", help="Output directory for PNG thumbnails"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Regenerate even if thumbnails exist"),
    dpi: int = typer.Option(100, "--dpi", help="PDF render DPI (higher = sharper but slower)"),
    max_width: int = typer.Option(800, "--max-width", help="Downscale to this max width in px"),
):
    """Generate PNG thumbnails for every asset in the catalog (LibreOffice → PDF → PNG).

    Slow (~3-5s per .pptx file). Run once after a scan; re-run with --overwrite if
    the library content changed."""
    from .library_thumbnails import generate_thumbnails, update_catalog_with_thumbnails

    catalog = load_catalog(catalog_path)
    console.print(f"[cyan]Generating thumbnails[/cyan] for {catalog.asset_count} assets…")
    console.print(f"[dim]Output dir: {thumbnails_dir}[/dim]")

    def progress(msg: str):
        console.print(f"  · {msg}")

    counts = generate_thumbnails(
        catalog, thumbnails_dir,
        dpi=dpi, max_width_px=max_width,
        overwrite=overwrite, progress_callback=progress,
    )
    console.print(f"\n[green]✓[/green] {counts['converted']} converted, "
                  f"{counts['skipped']} skipped (already exist), "
                  f"{counts['failed']} failed")

    # Update catalog with has_thumbnail flags
    updated = update_catalog_with_thumbnails(catalog, thumbnails_dir)
    save_catalog(catalog, catalog_path)
    console.print(f"  Catalog updated: {updated} entries flagged.")


@library_app.command("build-natives")
def library_build_natives(
    out: Path = typer.Option(
        Path("library/native"), "--out", "-o",
        help="Where to write native .pptx files (and matching thumbnails alongside).",
    ),
    catalog_path: Path = typer.Option(
        Path("library/catalog.json"), "--catalog",
        help="Existing catalog to merge native entries into.",
    ),
    generate_thumbnails: bool = typer.Option(
        True, "--thumbnails/--no-thumbnails",
        help="Render thumbnail PNGs via LibreOffice (skipped automatically if soffice is missing).",
    ),
):
    """Generate native shapes × themes + templates × themes as .pptx assets,
    add them to the library catalog with style_tags, and (optionally) render
    thumbnails. Idempotent: re-running replaces any existing 'native/*' entries.
    """
    from .native_assets.catalog_builder import build_native_catalog

    out_dir = out.parent if out.name == "native" else out
    # If the user pointed --out at .../native we still want the parent for the
    # thumbnails sibling directory; otherwise treat --out as the parent.
    base_dir = out.parent if out.name == "native" else out

    console.print(f"[cyan]Building natives[/cyan] under [bold]{base_dir}[/bold]…")
    new_natives = build_native_catalog(
        base_dir, generate_thumbnails=generate_thumbnails
    )
    console.print(f"[green]✓[/green] Generated {len(new_natives)} native assets")

    # Merge into catalog (idempotent on the "native/" prefix).
    if catalog_path.exists():
        catalog = load_catalog(catalog_path)
        before = len(catalog.assets)
        catalog.assets = [a for a in catalog.assets if not a.id.startswith("native/")]
        removed = before - len(catalog.assets)
        if removed:
            console.print(f"[dim]Removed {removed} previous native entries (idempotent merge)[/dim]")
        # Backfill auto style_tags on any existing entries that lack them
        # (older catalogs scanned before style_tags existed).
        from .library import auto_tag_asset
        backfilled = 0
        for a in catalog.assets:
            if not a.style_tags:
                a.style_tags = auto_tag_asset(a)
                backfilled += 1
        if backfilled:
            console.print(f"[dim]Auto-tagged {backfilled} existing entries that were missing style_tags[/dim]")
    else:
        from .library import LibraryCatalog
        catalog = LibraryCatalog(library_root=str(base_dir.resolve()))

    catalog.assets.extend(new_natives)
    catalog.asset_count = len(catalog.assets)
    new_categories = {a.category for a in new_natives}
    for c in new_categories:
        if c not in catalog.categories:
            catalog.categories.append(c)

    save_catalog(catalog, catalog_path)

    console.print(f"\n[bold]Catalog summary[/bold] → {catalog_path}")
    console.print(f"  Total assets: [bold]{catalog.asset_count}[/bold]")
    console.print(f"  Native entries: [bold]{len(new_natives)}[/bold]")
    console.print(f"  New categories: {', '.join(sorted(new_categories))}")
    if generate_thumbnails:
        thumbed = sum(1 for a in new_natives if a.has_thumbnail)
        console.print(f"  Thumbnails generated: [bold]{thumbed}/{len(new_natives)}[/bold]")
        if thumbed == 0:
            console.print(
                "[yellow]  (LibreOffice not detected — thumbnails skipped. "
                "Install with: brew install --cask libreoffice)[/yellow]"
            )


@template_app.command("ingest")
def template_ingest(
    pptx: Path = typer.Argument(..., help="Path to corporate .pptx file"),
    name: str = typer.Option(..., "--name", "-n", help="Template name (slugified for filename)"),
    description: str = typer.Option("", "--description", "-d", help="Optional description"),
):
    """Ingest a corporate .pptx and extract its design system as a slideAtelier template.

    Reads theme colors, fonts, master layouts, and footer text from the source file.
    Saves as templates/<slug>.json with master_pptx pointing at a copy of the source."""
    if not pptx.exists():
        console.print(f"[red]Not found:[/red] {pptx}")
        raise typer.Exit(1)
    config = Config.from_env()
    config.templates_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Ingesting[/cyan] {pptx}…")
    tpl = ingest_to_template(pptx, name, config.templates_dir, description=description)
    console.print(f"[green]✓[/green] Saved as template [bold]{tpl.name}[/bold]")
    console.print(f"  Colors: primary={tpl.colors.primary} accent={tpl.colors.accent}")
    console.print(f"  Fonts:  heading={tpl.fonts.heading} body={tpl.fonts.body}")
    if tpl.master_pptx:
        console.print(f"  Master: {tpl.master_pptx}")


@app.command()
def research(
    content: Path = typer.Option(..., "--content", "-c", help="Path to brief"),
    out: Path = typer.Option(Path("output/research.json"), "--out", "-o"),
    requirements: str = typer.Option("", "--requirements", "-r"),
):
    """Stage 0: AI research on a brief — produces a structured dossier of context, frameworks, hypothesis, and counter-arguments. Feeds the storyboard stage."""
    if not content.exists():
        console.print(f"[red]Brief not found:[/red] {content}")
        raise typer.Exit(1)
    config = Config.from_env()
    config.require_api_key()
    client = make_client(config)

    console.print(f"[cyan]Researching[/cyan] with {config.model}…")
    dossier, metadata = do_research(client, config, content.read_text(), requirements)
    console.print(f"[green]✓[/green] Dossier ready [dim]({metadata.duration_seconds:.1f}s)[/dim]")
    console.print(f"\n[bold]Hypothesis:[/bold] {dossier.hypothesis}")
    console.print(f"\n[bold]Key questions ({len(dossier.key_questions)}):[/bold]")
    for q in dossier.key_questions:
        console.print(f"  · {q}")
    console.print(f"\n[bold]Findings ({len(dossier.findings)}):[/bold]")
    for f in dossier.findings[:5]:
        console.print(f"  · {f.topic}: {f.insight[:80]}…")
    console.print(f"\n[bold]Counter-arguments:[/bold]")
    for c in dossier.counter_arguments:
        console.print(f"  ⚠ {c}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dossier.model_dump_json(indent=2))
    console.print(f"\n[green]✓[/green] Saved to [cyan]{out}[/cyan]")


@app.command()
def audit(
    job_id: str = typer.Argument(..., help="Workflow job ID (the deck.json under output/workflow/<job_id>/)"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of pretty table"),
    template: str | None = typer.Option(
        None, "--template", "-t", help="Template name or path. Defaults to job's recorded template."
    ),
):
    """Sprint V — Run the deck audit (linter) on a workflow job.

    Exits 0 if no error-severity issues; exits 1 otherwise — suitable for CI.
    Reads <output_dir>/workflow/<job_id>/deck.json.
    """
    import json as _json

    from .audit import audit_deck
    from .models import SlideDeck

    config = Config.from_env()
    job_dir = config.output_dir / "workflow" / job_id
    deck_path = job_dir / "deck.json"
    if not deck_path.exists():
        console.print(f"[red]deck.json not found:[/red] {deck_path}")
        raise typer.Exit(2)

    try:
        deck = SlideDeck.model_validate_json(deck_path.read_text())
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]deck.json invalid:[/red] {e}")
        raise typer.Exit(2) from e

    # Resolve template — prefer CLI flag, then job's stored template, then default.
    if template is None:
        name_path = job_dir / "template_name.txt"
        if name_path.exists():
            stored = name_path.read_text().strip()
            template = stored if stored else None
    tpl = _resolve_template(template, config)

    issues = audit_deck(deck, tpl)
    error_count = sum(1 for i in issues if i.severity == "error")

    if json_output:
        payload = {
            "job_id": job_id,
            "deck_title": deck.title,
            "issue_count": len(issues),
            "error_count": error_count,
            "issues": [i.model_dump() for i in issues],
        }
        # Use typer.echo (NOT rich console) so the output is plain JSON suitable
        # for piping. CliRunner captures typer.echo via Click's stdout proxy.
        typer.echo(_json.dumps(payload, indent=2))
    else:
        if not issues:
            console.print(f"[green]✓[/green] {deck.title!r} — no audit issues.")
        else:
            table = Table(title=f"Audit: {deck.title}")
            table.add_column("Slide", justify="right", style="cyan")
            table.add_column("Sev")
            table.add_column("Code", style="bold")
            table.add_column("Message", overflow="fold")
            for i in issues:
                sev_style = {
                    "error": "[red]error[/red]",
                    "warning": "[yellow]warn[/yellow]",
                    "info": "[dim]info[/dim]",
                }.get(i.severity, i.severity)
                table.add_row(
                    str(i.slide_idx + 1) if i.slide_idx >= 0 else "-",
                    sev_style,
                    i.code,
                    i.message,
                )
            console.print(table)
            console.print(
                f"[dim]{len(issues)} issue(s); {error_count} error(s).[/dim]"
            )

    raise typer.Exit(1 if error_count > 0 else 0)


# ---------------------------------------------------------------------------
# Invite codes — private-beta signup gate
# ---------------------------------------------------------------------------

@invite_app.command("create")
def invite_create(
    max_uses: int = typer.Option(1, "--max-uses", "-n", help="How many signups this code authorises (>=1; 0=unlimited, discouraged)"),
    expires_days: int | None = typer.Option(None, "--expires-days", "-e", help="Days until the code expires. Omit for no expiry."),
    code: str | None = typer.Option(None, "--code", help="Vanity code; omit to auto-generate via secrets.token_urlsafe(8)."),
):
    """Create a new invite code and print it.

    Stores into ${SLIDEATELIER_OUTPUT_DIR}/atelier.db (the same SQLite file the
    auth agent uses). Idempotent only when --code is supplied: re-running with
    the same code raises an error so you don't accidentally bump max_uses on a
    live invite.
    """
    from .auth import invites

    try:
        inv = invites.create_invite(max_uses=max_uses, expires_days=expires_days, code=code)
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from e
    console.print(f"[green]✓[/green] Created invite [bold cyan]{inv.code}[/bold cyan]")
    console.print(f"  max_uses:    [bold]{inv.max_uses if inv.max_uses else '∞'}[/bold]")
    console.print(f"  used_count:  [bold]{inv.used_count}[/bold]")
    console.print(f"  expires_at:  [bold]{inv.expires_at or 'never'}[/bold]")
    console.print()
    console.print(f"[dim]Share with the invitee — they enter this on /signup.[/dim]")


@invite_app.command("list")
def invite_list():
    """List every invite code with its remaining uses + expiry."""
    from .auth import invites

    rows = invites.list_invites()
    if not rows:
        console.print("[dim]No invites yet — create one with `atelier invite create`.[/dim]")
        return
    table = Table()
    table.add_column("Code", style="cyan", no_wrap=True)
    table.add_column("Used / Max", justify="right")
    table.add_column("Created")
    table.add_column("Expires")
    for r in rows:
        max_disp = "∞" if r.is_unlimited else str(r.max_uses)
        used_disp = f"{r.used_count} / {max_disp}"
        expires = r.expires_at or "never"
        if r.is_expired():
            expires = f"[red]{expires} (expired)[/red]"
        elif r.is_exhausted():
            used_disp = f"[red]{used_disp}[/red]"
        table.add_row(r.code, used_disp, r.created_at, expires)
    console.print(table)


@invite_app.command("revoke")
def invite_revoke(code: str = typer.Argument(..., help="The invite code to disable.")):
    """Revoke an invite (force max_uses=used_count) so it can't be redeemed again."""
    from .auth import invites

    if invites.revoke_invite(code):
        console.print(f"[green]✓[/green] Revoked [bold cyan]{code}[/bold cyan].")
    else:
        console.print(f"[yellow]No such invite: {code}[/yellow]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
