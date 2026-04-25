from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class RocketRideConfig:
	uri: str
	apikey: str
	openai_key: str
	anthropic_key: str
	gemini_key: str


def load_config() -> RocketRideConfig:
	"""
	Loads config from `.env` / environment.

	Notes:
	- RocketRide engine/SDK uses `ROCKETRIDE_*` variables for substitution inside `.pipe`.
	- We keep API keys as `ROCKETRIDE_*` to match the pipeline file.
	"""
	load_dotenv()

	return RocketRideConfig(
		uri=os.getenv("ROCKETRIDE_URI", "ws://localhost:5565"),
		apikey=os.getenv("ROCKETRIDE_APIKEY", ""),
		openai_key=os.getenv("ROCKETRIDE_OPENAI_KEY", ""),
		anthropic_key=os.getenv("ROCKETRIDE_ANTHROPIC_KEY", ""),
		gemini_key=os.getenv("ROCKETRIDE_GEMINI_KEY", ""),
	)

