"""Build README charts from PostgreSQL views. Draft prepared for joint review."""

from decimal import Decimal

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import psycopg
from psycopg.rows import dict_row

from load_raw import PROJECT_DIR, read_settings


OUTPUT_DIR = PROJECT_DIR / "docs" / "images"
MIN_VIEW_SESSIONS = 1000
ACCENT = "#166B70"
SECONDARY = "#8299AE"
TEXT = "#192D40"
MUTED = "#526577"


def format_count(value):
    return f"{value:,.0f}"


def format_percent(value):
    if value is None:
        return "—"
    return f"{100 * value:.2f}%"


def plot_funnel(summary):
    counts = [summary["view_sessions"], summary["cart_sessions"], summary["purchase_sessions"]]
    labels = ["View", "Cart", "Purchase"]
    fig, axis = plt.subplots(figsize=(12, 5.4))
    fig.subplots_adjust(left=0.16, right=0.93, top=0.73, bottom=0.20)
    fig.text(0.04, 0.91, "How many sessions reach a purchase", fontsize=22, weight="bold", color=TEXT)
    fig.text(0.04, 0.83, "Ordered steps within a session — view → cart → purchase",
             fontsize=12, color=MUTED)

    axis.barh(labels, counts, color=["#436A8B", ACCENT, "#715986"], height=0.52)
    axis.invert_yaxis()
    axis.set_xlim(0, max(counts[0], 1) * 1.32)
    axis.set_xlabel("Number of sessions", color=MUTED, labelpad=10)
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: format_count(value)))
    axis.set_axisbelow(True)
    axis.grid(axis="x", color="#E5EAF0", linewidth=0.8)
    axis.tick_params(axis="both", length=0, labelsize=11)
    for spine in axis.spines.values():
        spine.set_visible(False)
    for index, count in enumerate(counts):
        share = count / counts[0] if counts[0] else 0
        axis.text(count + max(counts[0], 1) * 0.025, index,
                  f"{format_count(count)}  ·  {format_percent(share)}",
                  va="center", fontsize=12, weight="bold", color=TEXT)

    fig.text(0.04, 0.07, "All percentages use " + format_count(counts[0]) + " sessions with a view as the base.",
             fontsize=12, color=MUTED)
    fig.savefig(OUTPUT_DIR / "purchase-funnel.png", dpi=160, facecolor="white")
    plt.close(fig)


def plot_categories(categories, coverage):
    total_views = sum(row["view_sessions"] for row in categories)
    total_carts = sum(row["cart_sessions"] for row in categories)
    baseline = Decimal(total_carts) / Decimal(total_views) if total_views else Decimal(0)
    baseline_percent = float(100 * baseline)
    candidates = [row for row in categories
                  if row["view_sessions"] >= MIN_VIEW_SESSIONS
                  and row["cart_sessions"] * total_views
                  < total_carts * row["view_sessions"]]
    candidates.sort(key=lambda row: (-row["sessions_without_cart"], row["category_code"]))
    candidates = candidates[:5]
    if not candidates:
        raise ValueError("No categories meet the chart selection rule")

    names = {
        "electronics.telephone": "Phones",
        "electronics.video.tv": "TVs",
        "stationery.cartrige": "Printer cartridges",
        "electronics.audio.acoustic": "Speakers",
        "computers.notebook": "Laptops",
    }
    labels = [names.get(row["category_code"], row["category_code"]) for row in candidates]
    rates = [float(row["view_to_cart_conversion"]) * 100 for row in candidates]
    counts = [row["sessions_without_cart"] for row in candidates]
    positions = list(range(len(candidates)))
    colors = [ACCENT] + [SECONDARY] * (len(candidates) - 1)

    fig, (rate_axis, count_axis) = plt.subplots(
        1, 2, figsize=(12, 7.2), sharey=True, gridspec_kw={"width_ratios": [1, 1.15]}
    )
    fig.subplots_adjust(left=0.20, right=0.96, top=0.72, bottom=0.27, wspace=0.19)
    fig.text(0.035, 0.92, "Add-to-cart conversion by category", fontsize=22, weight="bold", color=TEXT)
    fig.text(0.035, 0.85, "Count sessions with a product view within each category",
             fontsize=12, color=MUTED)

    rate_axis.barh(positions, rates, color=colors, height=0.54)
    rate_axis.set_yticks(positions, labels, fontsize=12)
    rate_axis.invert_yaxis()
    rate_axis.set_xlim(0, max(baseline_percent, *rates) * 1.24)
    rate_axis.axvline(baseline_percent, color=TEXT, linestyle="--", linewidth=1.2)
    rate_axis.set_title("Added to cart, %", loc="left", fontsize=12, pad=20, color=TEXT)
    rate_axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}%"))
    count_axis.barh(positions, counts, color=colors, height=0.54)
    count_axis.set_xlim(0, max(counts) * 1.3)
    count_axis.set_title("Sessions without\nan add-to-cart step", loc="left", fontsize=12, pad=20, color=TEXT)
    count_axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: format_count(value)))
    count_axis.tick_params(labelleft=False)

    for axis in (rate_axis, count_axis):
        axis.set_axisbelow(True)
        axis.grid(axis="x", color="#E5EAF0", linewidth=0.8)
        axis.tick_params(axis="x", length=0, labelsize=11)
        axis.tick_params(axis="y", length=0)
        for spine in axis.spines.values():
            spine.set_visible(False)
    for position, row in enumerate(candidates):
        rate_axis.text(rates[position] - 0.2, position,
                       format_percent(row["view_to_cart_conversion"]),
                       ha="right", va="center", fontsize=13, weight="bold",
                       color="white" if position == 0 else TEXT)
        count_axis.text(counts[position] + max(counts) * 0.025, position,
                        format_count(counts[position]),
                        va="center", fontsize=12, weight="bold", color=TEXT)

    fig.text(0.035, 0.17, "Dashed line — add-to-cart rate across named categories, " + format_percent(baseline),
             fontsize=12, color=TEXT)
    fig.text(0.035, 0.115,
             f"Categories shown have at least {format_count(MIN_VIEW_SESSIONS)} viewing sessions and a rate below the dashed line.",
             fontsize=11, color=MUTED)
    fig.text(0.035, 0.07, "Ranked by sessions without a subsequent add-to-cart event, highest first.",
             fontsize=11, color=MUTED)
    fig.text(0.035, 0.025,
             format_percent(coverage["unnamed_category_share"])
             + " of events in accepted sessions have no category name.",
             fontsize=10, color=MUTED)
    fig.savefig(OUTPUT_DIR / "category-cart-conversion.png", dpi=160, facecolor="white")
    plt.close(fig)


def create_charts(connection):
    """Read the analytical views and save two README charts."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM mart.funnel_summary")
        summary = cursor.fetchone()
        cursor.execute("SELECT * FROM mart.category_conversion")
        categories = cursor.fetchall()
        cursor.execute("SELECT * FROM mart.category_data_quality")
        coverage = cursor.fetchone()
    if not summary or not summary["view_sessions"] or not categories:
        raise ValueError("Charts require populated store and category views")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": TEXT})
    plot_categories(categories, coverage)
    plot_funnel(summary)


def main():
    settings = read_settings()
    with psycopg.connect(
        host=settings.get("POSTGRES_HOST", "localhost"),
        port=int(settings["POSTGRES_PORT"]),
        dbname=settings["POSTGRES_DB"],
        user=settings["POSTGRES_USER"],
        password=settings["POSTGRES_PASSWORD"],
    ) as connection:
        create_charts(connection)
    print(f"Charts saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
