from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt

from .template import (
    Template,
    TemplateColors,
    TemplateFonts,
    TemplateFooter,
    TemplateLogo,
    is_valid_hex,
    parse_hex,
    save_template,
)

console = Console()


def _ask_hex(label: str, default: str) -> str:
    while True:
        value = Prompt.ask(label, default=default)
        if is_valid_hex(value):
            return parse_hex(value)
        console.print(f"[red]Not a valid hex color: {value!r}. Try '#1F3A5F' or '1F3A5F'.[/red]")


def create_template_interactive(out_path: Path) -> Template:
    """Interactive Q&A → Template JSON. Returns the Template and writes it to out_path."""
    console.print()
    console.print("[bold cyan]slideAtelier — template creator[/bold cyan]")
    console.print(
        "[dim]Walks you through brand colors, fonts, and identity. "
        "Press Enter to accept defaults shown in []. All values can be edited later in the JSON file.[/dim]"
    )
    console.print()

    name = Prompt.ask("Template name", default="Custom")
    description = Prompt.ask("One-line description (optional)", default="")

    console.print()
    console.print("[bold]Brand colors[/bold] [dim](enter as #RRGGBB or RRGGBB)[/dim]")
    primary = _ask_hex("  Primary (titles, headers)", "#1F3A5F")
    accent = _ask_hex("  Accent (highlights, callouts)", "#C86E3C")
    text = _ask_hex("  Body text", "#222222")
    muted = _ask_hex("  Muted (subtitles, captions)", "#6B6B6B")
    background = _ask_hex("  Slide background", "#FFFFFF")

    console.print()
    use_status_colors = Confirm.ask(
        "Customize status colors (success/warning/danger)?", default=False
    )
    if use_status_colors:
        success = _ask_hex("  Success (green)", "#2E7D5B")
        warning = _ask_hex("  Warning (amber)", "#C9941F")
        danger = _ask_hex("  Danger (red)", "#B23A3A")
    else:
        success, warning, danger = "#2E7D5B", "#C9941F", "#B23A3A"

    console.print()
    console.print("[bold]Fonts[/bold]")
    console.print(
        "[dim]Calibri is the safest default — installed everywhere. "
        "If you choose a non-standard font (Inter, Fraunces, etc.) the viewer needs it installed.[/dim]"
    )
    heading_font = Prompt.ask("  Heading font", default="Calibri")
    body_font = Prompt.ask("  Body font", default="Calibri")

    console.print()
    use_logo = Confirm.ask("Add a logo to slides?", default=False)
    logo = TemplateLogo()
    if use_logo:
        logo_path = Prompt.ask("  Logo path (PNG or JPEG)", default="")
        position = Prompt.ask(
            "  Logo position",
            choices=["top-left", "top-right", "bottom-left", "bottom-right"],
            default="top-right",
        )
        width = float(Prompt.ask("  Logo width (inches)", default="1.0"))
        logo = TemplateLogo(path=logo_path or None, position=position, width_inches=width)

    console.print()
    use_footer = Confirm.ask("Add a footer (text + page numbers)?", default=False)
    footer = TemplateFooter()
    if use_footer:
        footer_text = Prompt.ask("  Footer text (e.g., 'Confidential — Acme Corp')", default="")
        show_pages = Confirm.ask("  Show page numbers?", default=True)
        footer = TemplateFooter(enabled=True, text=footer_text, show_page_numbers=show_pages)

    template = Template(
        name=name,
        description=description,
        colors=TemplateColors(
            primary=primary,
            accent=accent,
            text=text,
            muted=muted,
            background=background,
            success=success,
            warning=warning,
            danger=danger,
        ),
        fonts=TemplateFonts(heading=heading_font, body=body_font),
        logo=logo,
        footer=footer,
    )

    save_template(template, out_path)
    console.print()
    console.print(f"[green]✓[/green] Template saved to [cyan]{out_path}[/cyan]")
    console.print()
    console.print(
        f"[dim]Use it with: [bold]uv run atelier generate -c brief.md -t {out_path} -o output/deck.pptx[/bold][/dim]"
    )

    return template
