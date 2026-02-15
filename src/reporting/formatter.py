from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _make_console() -> Console:
    return Console(file=StringIO(), force_terminal=True, width=60)


def format_leaderboard(entries: list[dict]) -> str:
    table = Table(title="LEADERBOARD", show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", width=3)
    table.add_column("LLM", width=12)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("Win Rate", justify="right", width=10)
    table.add_column("Avg Return", justify="right", width=10)

    for entry in entries:
        pnl = float(entry["pnl"])
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        table.add_row(
            str(entry["rank"]),
            entry["llm_name"],
            pnl_str,
            f"{entry['win_rate'] * 100:.1f}%",
            f"{entry['avg_return']:+.1f}%",
        )

    console = _make_console()
    console.print(table)
    return console.file.getvalue()


def format_portfolio_summary(summary: dict) -> str:
    invested = float(summary["total_invested"])
    value = float(summary["total_value"])
    pnl = float(summary["pnl"])
    return_pct = summary["return_pct"]

    pnl_sign = "+" if pnl >= 0 else ""
    lines = [
        "[bold]YOUR REAL PORTFOLIO[/bold]",
        f"  Total Invested:  [white]{invested:>10.2f}[/white]",
        f"  Current Value:   [white]{value:>10.2f}[/white]",
        f"  Real P&L:        [{'green' if pnl >= 0 else 'red'}]"
        f"{pnl_sign}{pnl:.2f} ({pnl_sign}{return_pct:.1f}%)[/{'green' if pnl >= 0 else 'red'}]",
    ]

    console = _make_console()
    console.print("\n".join(lines))
    return console.file.getvalue()


def format_best_worst(best_worst: dict) -> str:
    lines: list[str] = []
    best = best_worst.get("best")
    worst = best_worst.get("worst")

    if best:
        lines.append(
            f"[bold]BEST PICK:[/bold]  [green]{best['ticker']} "
            f"+{best['return_pct']:.1f}%[/green] ({best['llm']}, {best['date']})"
        )
    if worst:
        lines.append(
            f"[bold]WORST PICK:[/bold] [red]{worst['ticker']} "
            f"{worst['return_pct']:.1f}%[/red] ({worst['llm']}, {worst['date']})"
        )

    if not lines:
        return ""

    console = _make_console()
    console.print("\n".join(lines))
    return console.file.getvalue()


def format_full_report(
    period_label: str,
    leaderboard: str,
    summary: str,
    best_worst: str,
) -> str:
    content = Text.from_ansi(leaderboard + summary + best_worst)
    panel = Panel(content, title=period_label, border_style="bold blue", expand=False)

    console = _make_console()
    console.print(panel)
    return console.file.getvalue()


def print_report(
    period_label: str,
    leaderboard_entries: list[dict],
    portfolio_summary: dict,
    best_worst: dict,
) -> None:
    lb = format_leaderboard(leaderboard_entries)
    ps = format_portfolio_summary(portfolio_summary)
    bw = format_best_worst(best_worst)
    output = format_full_report(period_label, lb, ps, bw)

    console = Console()
    console.print(Text.from_ansi(output))
