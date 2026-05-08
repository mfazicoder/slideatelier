"""Build the singleton native registry by importing all shapes/themes/templates.

Adding a new shape: write a class in shapes_*.py, then register it here.
"""
from .base import NativeRegistry
from .themes import THEMES


def build_registry() -> NativeRegistry:
    reg = NativeRegistry()

    # ---- themes ----
    for t in THEMES:
        reg.register_theme(t)

    # ---- shapes ----
    from .shapes_matrix import Matrix2x2
    reg.register_shape(Matrix2x2())

    # Other shapes (funnel, hexagon-grid, value-chain) get registered as agents
    # land their modules. Each module exposes one or more AssetShape classes.
    try:
        from .shapes_funnel import Funnel  # noqa: F401
        reg.register_shape(Funnel())
    except ImportError:
        pass
    try:
        from .shapes_hexagon import HexagonGrid  # noqa: F401
        reg.register_shape(HexagonGrid())
    except ImportError:
        pass
    try:
        from .shapes_value_chain import ValueChain  # noqa: F401
        reg.register_shape(ValueChain())
    except ImportError:
        pass
    try:
        from .shapes_pyramid import Pyramid  # noqa: F401
        reg.register_shape(Pyramid())
    except ImportError:
        pass
    try:
        from .shapes_hub_spoke import HubSpoke  # noqa: F401
        reg.register_shape(HubSpoke())
    except ImportError:
        pass
    try:
        from .shapes_iceberg import Iceberg  # noqa: F401
        reg.register_shape(Iceberg())
    except ImportError:
        pass
    try:
        from .shapes_layered_circles import LayeredCircles  # noqa: F401
        reg.register_shape(LayeredCircles())
    except ImportError:
        pass
    try:
        from .shapes_kpi_hero import KpiHero  # noqa: F401
        reg.register_shape(KpiHero())
    except ImportError:
        pass
    try:
        from .shapes_chart_bar import ChartBar  # noqa: F401
        reg.register_shape(ChartBar())
    except ImportError:
        pass
    try:
        from .shapes_chart_donut import ChartDonut  # noqa: F401
        reg.register_shape(ChartDonut())
    except ImportError:
        pass
    try:
        from .shapes_ribbon import Ribbon  # noqa: F401
        reg.register_shape(Ribbon())
    except ImportError:
        pass
    try:
        from .shapes_comparison_columns import ComparisonColumns  # noqa: F401
        reg.register_shape(ComparisonColumns())
    except ImportError:
        pass
    try:
        from .shapes_timeline_dots import TimelineDots  # noqa: F401
        reg.register_shape(TimelineDots())
    except ImportError:
        pass

    # ---- slide templates ----
    try:
        from .templates_hero import HeroFullBleed  # noqa: F401
        reg.register_template(HeroFullBleed())
    except ImportError:
        pass
    try:
        from .templates_exec_summary import ExecSummary  # noqa: F401
        reg.register_template(ExecSummary())
    except ImportError:
        pass

    return reg


# Module-level singleton (lazy)
_REGISTRY: NativeRegistry | None = None


def get_registry() -> NativeRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY
