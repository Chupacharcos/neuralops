"""Contextual Bandit para selección de estrategias de email y contenido."""
import json
import math
from core.memory import upsert, query


def _load_arms(bandit_id: str) -> dict:
    results = query("bandit_arms", where={"bandit_id": bandit_id}, n_results=50)
    arms = {}
    for r in results:
        arm_id = r["id"].replace(f"{bandit_id}_", "", 1)
        arms[arm_id] = r["metadata"]
    return arms


def _save_arm(bandit_id: str, arm_id: str, data: dict):
    upsert("bandit_arms", f"{bandit_id}_{arm_id}", arm_id, {"bandit_id": bandit_id, **data})


def ucb1_select(bandit_id: str, arm_ids: list[str]) -> str:
    """Select best arm using UCB1 algorithm."""
    arms = _load_arms(bandit_id)
    total_pulls = sum(arms.get(a, {}).get("pulls", 0) for a in arm_ids)

    best_arm = arm_ids[0]
    best_score = -1

    for arm_id in arm_ids:
        arm = arms.get(arm_id, {"pulls": 0, "rewards": 0})
        pulls = arm.get("pulls", 0)
        rewards = arm.get("rewards", 0)

        if pulls == 0:
            return arm_id  # Explore unvisited arms first

        avg_reward = rewards / pulls
        exploration = math.sqrt(2 * math.log(max(total_pulls, 1)) / pulls)
        score = avg_reward + exploration

        if score > best_score:
            best_score = score
            best_arm = arm_id

    return best_arm


def update_reward(bandit_id: str, arm_id: str, reward: float):
    """Update arm stats after observing a reward (0.0-1.0)."""
    arms = _load_arms(bandit_id)
    arm = arms.get(arm_id, {"pulls": 0, "rewards": 0})
    arm["pulls"] = arm.get("pulls", 0) + 1
    arm["rewards"] = arm.get("rewards", 0) + reward
    _save_arm(bandit_id, arm_id, arm)


def get_stats(bandit_id: str) -> dict:
    """Return stats for all arms of a bandit."""
    arms = _load_arms(bandit_id)
    return {
        arm_id: {
            "pulls": data.get("pulls", 0),
            "avg_reward": round(data.get("rewards", 0) / max(data.get("pulls", 1), 1), 3),
        }
        for arm_id, data in arms.items()
    }
