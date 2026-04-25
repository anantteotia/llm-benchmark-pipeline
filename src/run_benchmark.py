from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table

from config import load_config


ROOT = Path(__file__).resolve().parents[1]
PIPE_PATH = ROOT / ".pipe" / "benchmark.pipe"
PROMPTS_PATH = ROOT / "src" / "prompts.json"
RESULTS_DIR = ROOT / "results"
RAW_RESULTS_PATH = RESULTS_DIR / "benchmark_results.json"
TMP_DIR = ROOT / ".tmp_inputs"


@dataclass
class ProviderResult:
	prompt_id: str
	provider: str
	latency_ms: float | None
	prompt_tokens: int | None
	completion_tokens: int | None
	total_tokens: int | None
	text_preview: str
	trace: dict[str, Any] | None = None


def _first_str(value: Any) -> str | None:
	if isinstance(value, str):
		return value
	if isinstance(value, dict):
		for v in value.values():
			s = _first_str(v)
			if s:
				return s
	if isinstance(value, list):
		for v in value:
			s = _first_str(v)
			if s:
				return s
	return None


def _walk(obj: Any) -> Iterable[Any]:
	yield obj
	if isinstance(obj, dict):
		for v in obj.values():
			yield from _walk(v)
	elif isinstance(obj, list):
		for v in obj:
			yield from _walk(v)


def _extract_usage_from_trace(trace: Any) -> dict[str, dict[str, int | float]]:
	"""
	Best-effort extraction of per-provider stats from RocketRide `_trace`.

	Trace schemas can evolve; we intentionally parse loosely:
	- Provider name: anything that looks like 'llm_openai', 'llm_anthropic', 'llm_gemini'
	- Token fields: prompt_tokens / completion_tokens / total_tokens or nested usage.{prompt_tokens,...}
	- Latency: latency_ms / duration_ms / elapsed_ms
	"""
	stats: dict[str, dict[str, int | float]] = {}

	def bump(provider: str, key: str, val: int | float) -> None:
		if provider not in stats:
			stats[provider] = {}
		# keep the max if repeated; avoids double-counting when trace contains nested summaries
		prev = stats[provider].get(key)
		if prev is None or float(val) > float(prev):
			stats[provider][key] = val

	for node in _walk(trace):
		if not isinstance(node, dict):
			continue

		provider = None
		for k in ("provider", "component", "service", "name", "id"):
			v = node.get(k)
			if isinstance(v, str) and v.startswith("llm_"):
				provider = v
				break
		if provider is None:
			continue

		# tokens
		for k in ("prompt_tokens", "promptTokens"):
			if isinstance(node.get(k), int):
				bump(provider, "prompt_tokens", node[k])
		for k in ("completion_tokens", "completionTokens"):
			if isinstance(node.get(k), int):
				bump(provider, "completion_tokens", node[k])
		for k in ("total_tokens", "totalTokens"):
			if isinstance(node.get(k), int):
				bump(provider, "total_tokens", node[k])

		usage = node.get("usage")
		if isinstance(usage, dict):
			for k in ("prompt_tokens", "promptTokens"):
				if isinstance(usage.get(k), int):
					bump(provider, "prompt_tokens", usage[k])
			for k in ("completion_tokens", "completionTokens"):
				if isinstance(usage.get(k), int):
					bump(provider, "completion_tokens", usage[k])
			for k in ("total_tokens", "totalTokens"):
				if isinstance(usage.get(k), int):
					bump(provider, "total_tokens", usage[k])

		# latency
		for k in ("latency_ms", "duration_ms", "elapsed_ms"):
			if isinstance(node.get(k), (int, float)):
				bump(provider, "latency_ms", float(node[k]))

	return stats


async def run_one_prompt(*, client: Any, token: str, prompt_id: str, prompt: str) -> list[ProviderResult]:
	TMP_DIR.mkdir(exist_ok=True)
	RESULTS_DIR.mkdir(exist_ok=True)

	tmp_path = TMP_DIR / f"{prompt_id}.txt"
	tmp_path.write_text(prompt, encoding="utf-8")

	start = time.perf_counter()
	upload_results = await client.send_files([(str(tmp_path), {"name": tmp_path.name}, "text/plain")], token)
	end = time.perf_counter()
	overall_latency_ms = (end - start) * 1000.0

	result_obj: dict[str, Any] | None = None
	for item in upload_results:
		if isinstance(item, dict) and item.get("action") == "complete":
			res = item.get("result")
			if isinstance(res, dict):
				result_obj = res
				break

	if not result_obj:
		raise RuntimeError(f"No pipeline result returned for prompt '{prompt_id}'. Raw upload results: {upload_results}")

	answers = result_obj.get("answers")
	trace = result_obj.get("_trace")

	usage_by_provider = _extract_usage_from_trace(trace) if trace is not None else {}

	# answers is typically a list; contents may be strings or rich objects
	if not isinstance(answers, list) or not answers:
		return [
			ProviderResult(
				prompt_id=prompt_id,
				provider="pipeline",
				latency_ms=overall_latency_ms,
				prompt_tokens=None,
				completion_tokens=None,
				total_tokens=None,
				text_preview=_first_str(result_obj) or "",
				trace=trace if isinstance(trace, dict) else None,
			)
		]

	# deterministic mapping: we know we asked for 3 providers in a fixed order
	provider_order = ["llm_openai", "llm_anthropic", "llm_gemini"]

	out: list[ProviderResult] = []
	for idx, answer in enumerate(answers):
		provider = provider_order[idx] if idx < len(provider_order) else f"provider_{idx+1}"
		stats = usage_by_provider.get(provider) or usage_by_provider.get(f"{provider}_1") or {}

		prompt_tokens = int(stats["prompt_tokens"]) if "prompt_tokens" in stats else None
		completion_tokens = int(stats["completion_tokens"]) if "completion_tokens" in stats else None
		total_tokens = int(stats["total_tokens"]) if "total_tokens" in stats else None
		latency_ms = float(stats["latency_ms"]) if "latency_ms" in stats else None

		if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
			total_tokens = prompt_tokens + completion_tokens

		text = _first_str(answer) or ""
		preview = (text[:140] + "…") if len(text) > 140 else text

		out.append(
			ProviderResult(
				prompt_id=prompt_id,
				provider=provider,
				latency_ms=latency_ms,
				prompt_tokens=prompt_tokens,
				completion_tokens=completion_tokens,
				total_tokens=total_tokens,
				text_preview=preview,
				trace=trace if isinstance(trace, dict) else None,
			)
		)

	return out


async def main() -> None:
	console = Console()
	cfg = load_config()

	if not cfg.uri:
		raise RuntimeError("Missing ROCKETRIDE_URI (e.g. ws://localhost:5565)")

	# Avoid printing secrets, but fail fast if they're missing
	missing = []
	for env_name, value in (
		("ROCKETRIDE_OPENAI_KEY", cfg.openai_key),
		("ROCKETRIDE_ANTHROPIC_KEY", cfg.anthropic_key),
		("ROCKETRIDE_GEMINI_KEY", cfg.gemini_key),
	):
		if not value:
			missing.append(env_name)
	if missing:
		raise RuntimeError(f"Missing required env vars in `.env`: {', '.join(missing)}")

	prompts = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
	if not isinstance(prompts, list) or not prompts:
		raise RuntimeError("`src/prompts.json` must be a non-empty JSON list.")

	from rocketride import RocketRideClient  # imported late so requirements are obvious

	all_rows: list[ProviderResult] = []

	async with RocketRideClient(uri=cfg.uri, auth=cfg.apikey) as client:
		use_kwargs = {"filepath": str(PIPE_PATH), "threads": 3, "pipelineTraceLevel": "full"}
		result = await client.use(**use_kwargs)
		token = result["token"]

		for item in prompts:
			prompt_id = str(item.get("id", "")).strip() or "prompt"
			prompt = str(item.get("prompt", "")).strip()
			if not prompt:
				continue

			console.print(f"[bold]Running[/bold] {prompt_id} …")
			rows = await run_one_prompt(client=client, token=token, prompt_id=prompt_id, prompt=prompt)
			all_rows.extend(rows)

		await client.terminate(token)

	RAW_RESULTS_PATH.write_text(
		json.dumps([r.__dict__ for r in all_rows], indent=2),
		encoding="utf-8",
	)

	table = Table(title="LLM Benchmark (RocketRide)")
	table.add_column("Prompt", style="bold")
	table.add_column("Provider")
	table.add_column("Latency (ms)", justify="right")
	table.add_column("Prompt tok", justify="right")
	table.add_column("Comp tok", justify="right")
	table.add_column("Total tok", justify="right")
	table.add_column("Preview")

	def fmt_num(v: int | float | None) -> str:
		if v is None:
			return "-"
		if isinstance(v, float):
			return f"{v:.0f}"
		return str(v)

	for r in all_rows:
		table.add_row(
			r.prompt_id,
			r.provider,
			fmt_num(r.latency_ms),
			fmt_num(r.prompt_tokens),
			fmt_num(r.completion_tokens),
			fmt_num(r.total_tokens),
			r.text_preview.replace("\n", " "),
		)

	console.print()
	console.print(table)
	console.print()
	console.print(f"Wrote raw results to [bold]{RAW_RESULTS_PATH}[/bold]")


if __name__ == "__main__":
	asyncio.run(main())

