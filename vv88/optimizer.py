"""Self-tuning: learn which style, clip length and hook type win on *your* accounts *this week*.

Each published post becomes a reward once it is `metric_age_hours` old:
    r = log(1 + views) - mean log(1 + views) on that platform
(so TikTok's bigger numbers don't drown out X). Rewards decay with a configurable half-life
(default 7 days), so the system tracks what is working now, not what worked last month.
Choices use Thompson sampling: arms that look good get most of the traffic while weaker
or untested arms still get enough exposure to be re-checked as trends shift.
"""

from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass

from vv88.config import OptimizerConfig

LENGTH_TARGETS = {"15-30": 24.0, "30-45": 38.0, "45-90": 55.0}
DIMENSIONS = ("style", "length_bucket", "hook_type")


@dataclass
class Arm:
    weight: float = 0.0
    total: float = 0.0
    n: int = 0

    def posterior(self, prior_strength: float, sigma: float) -> tuple[float, float]:
        mean = self.total / (prior_strength + self.weight)
        sd = sigma / math.sqrt(prior_strength + self.weight)
        return mean, sd


class Optimizer:
    def __init__(self, rows, cfg: OptimizerConfig, now: float | None = None, rng: random.Random | None = None,
                 prior_strength: float = 2.0):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.prior_strength = prior_strength
        self.now = time.time() if now is None else now
        self.arms: dict[str, dict[str, Arm]] = {d: defaultdict(Arm) for d in DIMENSIONS}
        self.sigma = 1.0
        self._fit(rows)

    def _fit(self, rows) -> None:
        min_age = self.cfg.metric_age_hours * 3600
        usable = [
            r for r in rows
            if r["views"] is not None and r["posted_at"] and self.now - r["posted_at"] >= min_age
        ]
        by_platform: dict[str, list[float]] = defaultdict(list)
        for r in usable:
            by_platform[r["platform"]].append(math.log1p(r["views"]))
        base = {p: sum(v) / len(v) for p, v in by_platform.items()}
        residuals = []
        for r in usable:
            res = math.log1p(r["views"]) - base[r["platform"]]
            age_days = (self.now - r["posted_at"]) / 86400
            w = 0.5 ** (age_days / self.cfg.half_life_days)
            residuals.append(res)
            for d in DIMENSIONS:
                arm = self.arms[d][r[d] or "unknown"]
                arm.weight += w
                arm.total += w * res
                arm.n += 1
        if len(residuals) >= 5:
            mu = sum(residuals) / len(residuals)
            self.sigma = max(0.3, math.sqrt(sum((x - mu) ** 2 for x in residuals) / (len(residuals) - 1)))

    def choose(self, dimension: str, options: list[str]) -> str:
        if not options:
            raise ValueError("no options")
        if len(options) == 1:
            return options[0]
        if self.rng.random() < self.cfg.explore_floor:
            return self.rng.choice(options)
        best, best_draw = options[0], -math.inf
        for opt in options:
            mean, sd = self.arms[dimension][opt].posterior(self.prior_strength, self.sigma)
            draw = self.rng.gauss(mean, sd)
            if draw > best_draw:
                best, best_draw = opt, draw
        return best

    def hook_bonus(self) -> dict[str, float]:
        """Score bonus per hook type for the highlight finder (only once there's evidence)."""
        out = {}
        for hook, arm in self.arms["hook_type"].items():
            if arm.n >= 3:
                mean, _ = arm.posterior(self.prior_strength, self.sigma)
                out[hook] = max(-1.0, min(1.0, mean))
        return out

    def report(self) -> dict[str, list[tuple[str, int, float]]]:
        out = {}
        for d in DIMENSIONS:
            rows = []
            for name, arm in self.arms[d].items():
                mean, _ = arm.posterior(self.prior_strength, self.sigma)
                rows.append((name, arm.n, round(math.expm1(mean) * 100, 1)))  # % vs platform average
            out[d] = sorted(rows, key=lambda x: x[2], reverse=True)
        return out
