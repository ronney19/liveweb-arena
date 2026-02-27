"""
Task ID Registry - Deterministic task generation for reproducible evaluations.

This module provides a stable mapping from task_id to question configurations.
Adding new templates only appends new combinations, never affecting existing task_ids.

Usage:
    # With task_id (deterministic)
    config = TaskRegistry.parse_task_id(12345)

    # Without task_id (random, backward compatible)
    config = TaskRegistry.random_config(seed=100, num_tasks=3)

=== IMPORTANT: Adding New Templates ===

To add new templates while preserving existing task_id mappings:

1. Add template to TEMPLATES dict with a NEW ID (not used before)
2. Add the new template ID to a NEW entry in TEMPLATE_VERSIONS list
3. Run TaskRegistry.rebuild_combinations() - new combos are appended at end

Example - adding a new "reddit" plugin with 2 templates:

    # In TEMPLATES dict:
    80: ("reddit", "reddit_top_post"),
    81: ("reddit", "reddit_comment_count"),

    # In TEMPLATE_VERSIONS list, add a new entry:
    TEMPLATE_VERSIONS = [
        [...],  # Version 1: original (DO NOT MODIFY)
        [70, 71, 72, 73],  # Version 2: hackernews (DO NOT MODIFY)
        [80, 81],  # Version 3: reddit (NEW)
    ]

Rules:
- NEVER modify existing TEMPLATE_VERSIONS entries
- NEVER reuse template IDs that were used before
- Always add new templates as a new version entry
"""

from itertools import combinations
from typing import Dict, List, Optional, Tuple, Any


class TaskRegistry:
    """Registry for deterministic task_id to question configuration mapping."""

    # Task IDs allocated per combination
    TASK_IDS_PER_COMBO = 10000

    # Maximum templates in a combination (1, 2, or 3)
    MAX_COMBO_SIZE = 3

    # Template registry: ID -> (plugin_name, template_name)
    # IDs are permanent, only append new ones
    TEMPLATES: Dict[int, Tuple[str, str]] = {
        # Weather templates
        1: ("weather", "location_name"),
        2: ("weather", "time_of_day"),
        3: ("weather", "multi_day"),
        4: ("weather", "current_weather"),
        5: ("weather", "astronomy"),
        6: ("weather", "weather_comparison"),

        # Stooq templates
        10: ("stooq", "stooq_price"),
        11: ("stooq", "stooq_comparison"),
        12: ("stooq", "stooq_ranking"),
        13: ("stooq", "stooq_sector_analysis"),
        15: ("stooq", "stooq_currency"),
        16: ("stooq", "stooq_volatility"),
        17: ("stooq", "stooq_range_position"),

        # Taostats templates
        20: ("taostats", "taostats_subnet_info"),
        21: ("taostats", "taostats_comparison"),
        22: ("taostats", "taostats_analysis"),
        23: ("taostats", "taostats_ranking"),
        24: ("taostats", "taostats_price_change"),
        25: ("taostats", "taostats_threshold"),
        26: ("taostats", "taostats_multi_condition"),
        27: ("taostats", "taostats_delta"),
        28: ("taostats", "taostats_range_count"),
        29: ("taostats", "taostats_percentage"),

        # CoinGecko templates
        30: ("coingecko", "coingecko_price"),
        31: ("coingecko", "coingecko_volume"),
        32: ("coingecko", "coingecko_comparison"),
        33: ("coingecko", "coingecko_rank"),
        34: ("coingecko", "coingecko_top_movers"),
        35: ("coingecko", "coingecko_supply"),
        36: ("coingecko", "coingecko_ath"),
        37: ("coingecko", "coingecko_performance"),

        # Hybrid cross-site templates
        50: ("hybrid", "hybrid_top_performer"),
        51: ("hybrid", "hybrid_ranking"),
        52: ("hybrid", "hybrid_conditional_branch"),
        53: ("hybrid", "hybrid_portfolio_rebalance"),
        # 54, 55, 57: removed (templates deleted)
        56: ("hybrid", "hybrid_anomaly_detection"),
        58: ("hybrid", "hybrid_chained_decision"),
        59: ("hybrid", "hybrid_cross_domain_calc"),
        60: ("hybrid", "hybrid_satisficing_search"),

        # Hacker News templates (IDs 70+ to preserve existing task_id mappings)
        75: ("hackernews", "hackernews_multi_condition_filter"),
        76: ("hackernews", "hackernews_extrema_comparison"),
        77: ("hackernews", "hackernews_category_comparison"),
        78: ("hackernews", "hackernews_news_summary"),

        # Add new templates here with new IDs (80+)...
    }

    # Template versions - each version's combinations come AFTER all previous versions
    # This ensures existing task_ids are never affected when adding new templates.
    #
    # === RULES ===
    # 1. NEVER modify existing entries - only append new entries
    # 2. Each entry is a list of template IDs added in that version
    # 3. Template IDs must be unique across ALL versions
    #
    # Combination order:
    # - First: all combos using only Version 1 IDs
    # - Then: combos involving at least one Version 2 ID
    # - Then: combos involving at least one Version 3 ID
    # - etc.
    TEMPLATE_VERSIONS: List[List[int]] = [
        # Version 1: Original templates (frozen - DO NOT MODIFY)
        [1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 15, 16, 17, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 50, 51, 52, 53, 56, 58, 59, 60],
        # Version 2: Hacker News templates
        [75, 76, 77, 78],
        # Add new versions here...
    ]

    # Combination registry: list of template ID tuples
    # Order is permanent, only append new combinations
    _combinations: List[Tuple[int, ...]] = []
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls):
        """Ensure combinations are initialized."""
        if not cls._initialized:
            cls.rebuild_combinations()

    @classmethod
    def rebuild_combinations(cls):
        """
        Build all template combinations in a stable, version-aware order.

        Combinations are generated version by version:
        1. All combinations using only Version 1 template IDs
        2. Combinations involving at least one Version 2 ID (may include V1 IDs)
        3. Combinations involving at least one Version 3 ID (may include V1, V2 IDs)
        4. etc.

        This ensures:
        - Existing task_ids always map to the same questions
        - New templates only add new task_ids at the end
        - No need to track STABLE_MAX_ID - versions handle it automatically
        """
        all_ids = sorted(cls.TEMPLATES.keys())
        new_combinations = []

        # Track which IDs have been "seen" (from previous versions)
        seen_ids = set()

        for version_idx, version_ids in enumerate(cls.TEMPLATE_VERSIONS):
            version_ids_set = set(version_ids)

            if version_idx == 0:
                # Version 1: generate all combinations using only these IDs
                v1_ids = sorted(version_ids)
                for size in range(1, cls.MAX_COMBO_SIZE + 1):
                    for combo in combinations(v1_ids, size):
                        new_combinations.append(combo)
            else:
                # Later versions: generate combinations involving at least one NEW ID
                # (can include IDs from previous versions)
                all_seen_ids = sorted(seen_ids | version_ids_set)
                for size in range(1, cls.MAX_COMBO_SIZE + 1):
                    for combo in combinations(all_seen_ids, size):
                        # Include only if at least one ID is from this version
                        if any(tid in version_ids_set for tid in combo):
                            new_combinations.append(combo)

            # Mark this version's IDs as seen
            seen_ids.update(version_ids_set)

        cls._combinations = new_combinations
        cls._initialized = True

    @classmethod
    def max_task_id(cls) -> int:
        """Get the maximum valid task_id."""
        cls._ensure_initialized()
        return len(cls._combinations) * cls.TASK_IDS_PER_COMBO

    @classmethod
    def parse_task_id(cls, task_id: int) -> Dict[str, Any]:
        """
        Parse a task_id into its configuration.

        Args:
            task_id: The task ID (1 to max_task_id)

        Returns:
            Dict with:
            - task_id: The original task_id
            - combo_index: Index into combinations list
            - template_ids: Tuple of template IDs in this combination
            - templates: List of (plugin, template_name) tuples
            - variation_seed: Seed for variation within this combination
            - num_tasks: Number of sub-tasks (3-5)

        Raises:
            ValueError: If task_id is out of valid range
        """
        cls._ensure_initialized()

        if task_id < 1:
            raise ValueError("task_id must be >= 1")

        combo_index = (task_id - 1) // cls.TASK_IDS_PER_COMBO
        variation_seed = (task_id - 1) % cls.TASK_IDS_PER_COMBO

        if combo_index >= len(cls._combinations):
            raise ValueError(
                f"task_id {task_id} out of range. "
                f"Valid range: 1 - {cls.max_task_id()}"
            )

        template_ids = cls._combinations[combo_index]
        templates = [cls.TEMPLATES[tid] for tid in template_ids]

        # Check for disabled plugins
        from liveweb_arena.plugins import DISABLED_PLUGINS
        disabled_in_combo = [
            (tid, plugin) for tid, (plugin, _) in zip(template_ids, templates)
            if plugin in DISABLED_PLUGINS
        ]
        if disabled_in_combo:
            names = ", ".join(f"{plugin}(id={tid})" for tid, plugin in disabled_in_combo)
            raise ValueError(
                f"task_id {task_id} includes disabled plugin(s): {names}. "
                f"Choose a different task_id."
            )

        num_tasks = (variation_seed % 3) + 2

        return {
            "task_id": task_id,
            "combo_index": combo_index,
            "template_ids": template_ids,
            "templates": templates,
            "variation_seed": variation_seed,
            "num_tasks": num_tasks,
        }

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Get registry statistics."""
        cls._ensure_initialized()

        combo_by_size = {}
        for combo in cls._combinations:
            size = len(combo)
            combo_by_size[size] = combo_by_size.get(size, 0) + 1

        return {
            "num_templates": len(cls.TEMPLATES),
            "num_combinations": len(cls._combinations),
            "max_task_id": cls.max_task_id(),
            "task_ids_per_combo": cls.TASK_IDS_PER_COMBO,
            "combinations_by_size": combo_by_size,
        }

    @classmethod
    def print_info(cls):
        """Print registry information."""
        stats = cls.get_stats()
        print("=" * 50)
        print("Task Registry Info")
        print("=" * 50)
        print(f"Templates: {stats['num_templates']}")
        print(f"Combinations: {stats['num_combinations']}")
        print(f"Max task_id: {stats['max_task_id']}")
        print(f"Task IDs per combo: {stats['task_ids_per_combo']}")
        print(f"Combinations by size: {stats['combinations_by_size']}")
        print()
        print("Template List:")
        for tid, (plugin, name) in sorted(cls.TEMPLATES.items()):
            print(f"  {tid:3d}: {plugin}/{name}")


# Convenience functions for external use
def parse_task_id(task_id: int) -> Dict[str, Any]:
    """Parse a task_id into configuration. See TaskRegistry.parse_task_id."""
    return TaskRegistry.parse_task_id(task_id)


def max_task_id() -> int:
    """Get maximum valid task_id."""
    return TaskRegistry.max_task_id()


# Initialize on import
TaskRegistry._ensure_initialized()


if __name__ == "__main__":
    # Demo
    TaskRegistry.print_info()

    print("\nExample task_id parsing:")
    for tid in [1, 10001, 50001, 100000]:
        try:
            config = parse_task_id(tid)
            print(f"\ntask_id={tid}:")
            print(f"  templates: {config['templates']}")
            print(f"  num_tasks: {config['num_tasks']}")
            print(f"  variation_seed: {config['variation_seed']}")
        except ValueError as e:
            print(f"\ntask_id={tid}: {e}")
