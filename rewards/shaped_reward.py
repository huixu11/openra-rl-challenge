"""Shaped reward for evaluating OpenRA-RL agents.

This is NOT used during training — it is used to score completed episodes
for the results table in the blog post and for comparing agents.

Usage:
    from rewards.shaped_reward import EvalReward

    reward_fn = EvalReward()

    # Score a completed episode
    final_obs = trajectory[-1]["observation"]
    scores = reward_fn.score(final_obs)
    total = reward_fn.total(final_obs)
"""

from typing import Optional


class EvalReward:
    """Score a completed OpenRA-RL episode across multiple dimensions.

    Components (all normalized to roughly [0, 1]):
    - exploration: fraction of map explored
    - base_progress: number of buildings built (normalized)
    - army_strength: number of units trained (normalized)
    - combat_ratio: kill/death cost ratio
    - survival: how long the agent survived (fraction of typical game)
    - outcome: 1.0 for win, 0.0 for draw/timeout, -0.3 for loss
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
        max_buildings: int = 10,
        max_units: int = 15,
        max_ticks: int = 30000,
    ):
        self.weights = weights or {
            "exploration": 0.20,
            "base_progress": 0.20,
            "army_strength": 0.15,
            "combat_ratio": 0.20,
            "survival": 0.10,
            "outcome": 0.15,
        }
        self.max_buildings = max_buildings
        self.max_units = max_units
        self.max_ticks = max_ticks

    def score(self, final_obs: dict) -> dict[str, float]:
        """Compute individual reward components from a final observation.

        Args:
            final_obs: The last observation dict from an episode.

        Returns:
            Dict of component name → score.
        """
        # Exploration: fraction of map explored
        explored = final_obs.get("explored_percent", 0)
        if isinstance(explored, (int, float)):
            exploration = min(1.0, explored / 100.0)
        else:
            exploration = 0.0

        # Base progress: buildings constructed
        n_buildings = len(final_obs.get("buildings", []))
        building_types = len(set(b["type"] for b in final_obs.get("buildings", [])))
        base_progress = min(1.0, (n_buildings + building_types) / (self.max_buildings + 5))

        # Army: units trained
        n_units = len(final_obs.get("units", []))
        army_strength = min(1.0, n_units / self.max_units)

        # Combat: kill/death cost ratio
        mil = final_obs.get("military", {})
        kills_cost = mil.get("kills_cost", 0)
        deaths_cost = mil.get("deaths_cost", 0)
        if kills_cost + deaths_cost > 0:
            combat_ratio = kills_cost / (kills_cost + deaths_cost)
        else:
            combat_ratio = 0.0

        # Survival: proportion of max ticks survived
        survival = min(1.0, final_obs.get("tick", 0) / self.max_ticks)

        # Outcome: game result
        result = final_obs.get("result", "")
        if result == "win":
            outcome = 1.0
        elif result == "lose":
            outcome = -0.3
        else:
            outcome = 0.0

        return {
            "exploration": round(exploration, 3),
            "base_progress": round(base_progress, 3),
            "army_strength": round(army_strength, 3),
            "combat_ratio": round(combat_ratio, 3),
            "survival": round(survival, 3),
            "outcome": round(outcome, 3),
        }

    def total(self, final_obs: dict) -> float:
        """Compute the weighted total reward score.

        Args:
            final_obs: The last observation dict from an episode.

        Returns:
            Weighted sum of all components.
        """
        scores = self.score(final_obs)
        total = sum(scores[k] * self.weights.get(k, 0) for k in scores)
        return round(total, 4)

    def score_trajectory(self, trajectory: list[dict]) -> dict:
        """Score a full trajectory (list of step dicts).

        Convenience method that extracts the final observation
        and computes both per-component and total scores.

        Args:
            trajectory: List of {observation, action, reward} dicts.

        Returns:
            Dict with 'components' and 'total' keys.
        """
        if not trajectory:
            return {"components": {}, "total": 0.0}

        # Find the last observation
        final_obs = trajectory[-1].get("observation", {})

        return {
            "components": self.score(final_obs),
            "total": self.total(final_obs),
        }

    def compare(self, episodes: list[list[dict]]) -> dict:
        """Compare multiple episodes and return aggregate statistics.

        Args:
            episodes: List of trajectories (each a list of step dicts).

        Returns:
            Dict with per-episode scores and averages.
        """
        results = []
        for i, traj in enumerate(episodes):
            score = self.score_trajectory(traj)
            score["episode"] = i + 1
            results.append(score)

        # Compute averages
        if results:
            component_keys = list(results[0]["components"].keys())
            averages = {
                k: round(sum(r["components"][k] for r in results) / len(results), 3)
                for k in component_keys
            }
            avg_total = round(sum(r["total"] for r in results) / len(results), 4)
        else:
            averages = {}
            avg_total = 0.0

        return {
            "episodes": results,
            "averages": averages,
            "average_total": avg_total,
            "num_episodes": len(results),
        }
