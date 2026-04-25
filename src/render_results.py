from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table


ROOT = Path(__file__).resolve().parents[1]
RAW_RESULTS_PATH = ROOT / "results" / "benchmark_results.json"


def main() -> None:
	console = Console()

	if not RAW_RESULTS_PATH.exists():
		raise SystemExit(f"Missing {RAW_RESULTS_PATH}. Run src/run_benchmark.py at least once first.")

	rows = json.loads(RAW_RESULTS_PATH.read_text(encoding="utf-8"))
	if not isinstance(rows, list) or not rows:
		raise SystemExit(f"{RAW_RESULTS_PATH} is empty or invalid.")

	table = Table(title="LLM Benchmark (RocketRide) — saved results")
	table.add_column("Prompt", style="bold")
	table.add_column("Provider")
	table.add_column("Latency (ms)", justify="right")
	table.add_column("Prompt tok", justify="right")
	table.add_column("Comp tok", justify="right")
	table.add_column("Total tok", justify="right")
	table.add_column("Preview")

	def fmt(v) -> str:
		if v is None:
			return "-"
		if isinstance(v, float):
			return f"{v:.0f}"
		return str(v)

	for r in rows:
		table.add_row(
			str(r.get("prompt_id", "")),
			str(r.get("provider", "")),
			fmt(r.get("latency_ms")),
			fmt(r.get("prompt_tokens")),
			fmt(r.get("completion_tokens")),
			fmt(r.get("total_tokens")),
			str(r.get("text_preview", "")).replace("\n", " "),
		)

	console.print()
	console.print(table)
	console.print()
	console.print(f"Rendered from {RAW_RESULTS_PATH}")


if __name__ == "__main__":
	main()

