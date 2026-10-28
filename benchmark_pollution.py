"""Repeatable LRU-versus-Clock cache-pollution workload suite."""
from __future__ import annotations

import csv
import os
import random
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from buffer_pool_manager import BufferPoolManager
from disk_manager import DiskManager


DEFAULT_SCENARIOS = (
    {"name": "baseline", "pool_size": 10, "num_hot_pages": 3, "num_cold_pages": 8},
    {"name": "larger_hot_set", "pool_size": 10, "num_hot_pages": 4, "num_cold_pages": 8},
    {"name": "longer_scan", "pool_size": 10, "num_hot_pages": 3, "num_cold_pages": 12},
    {"name": "larger_pool", "pool_size": 16, "num_hot_pages": 4, "num_cold_pages": 14},
)
DEFAULT_SEEDS = (42, 43, 44, 45, 46)


def run_cache_pollution_experiment(
    pool_size: int = 10,
    num_hot_pages: int = 3,
    num_cold_pages: int = 8,
    num_iterations: int = 20,
    hot_queries_per_iteration: int = 10,
    seed: int = 42,
) -> tuple[dict, list[dict]]:
    """Run one paired LRU/Clock experiment with an identical seeded trace."""
    if pool_size < 1:
        raise ValueError("pool_size must be positive")
    if not 1 <= num_hot_pages <= pool_size:
        raise ValueError("num_hot_pages must be between 1 and pool_size")
    if num_cold_pages < 1 or num_iterations < 1 or hot_queries_per_iteration < 1:
        raise ValueError("cold pages, iterations, and hot queries per iteration must be positive")

    results: dict[str, dict] = {}
    timeline: list[dict] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        disk_manager = DiskManager(str(Path(temp_dir) / "benchmark.db"))
        try:
            hot_page_ids = [disk_manager.allocate_page() for _ in range(num_hot_pages)]
            cold_page_ids = [disk_manager.allocate_page() for _ in range(num_cold_pages)]
            rng = random.Random(seed)
            hot_workload = [
                [rng.choice(hot_page_ids) for _ in range(hot_queries_per_iteration)]
                for _ in range(num_iterations)
            ]

            for policy in ("lru", "clock"):
                bpm = BufferPoolManager(pool_size, disk_manager, policy)
                for page_id in hot_page_ids:
                    bpm.fetch_page(page_id)
                    bpm.unpin_page(page_id)
                bpm.reset_metrics()

                hot_hits = 0
                hot_requests = 0
                started = time.perf_counter()
                for iteration, hot_queries in enumerate(hot_workload, start=1):
                    iteration_hot_hits = 0
                    for page_id in hot_queries:
                        before = bpm.hits
                        page = bpm.fetch_page(page_id)
                        if page is None:
                            raise RuntimeError("buffer pool could not fetch an unpinned page")
                        was_hit = bpm.hits > before
                        hot_hits += was_hit
                        iteration_hot_hits += was_hit
                        hot_requests += 1
                        bpm.unpin_page(page_id)

                    for page_id in cold_page_ids:
                        page = bpm.fetch_page(page_id)
                        if page is None:
                            raise RuntimeError("buffer pool could not fetch an unpinned page")
                        bpm.unpin_page(page_id)

                    timeline.append({
                        "policy": policy.upper(),
                        "seed": seed,
                        "iteration": iteration,
                        "hot_hit_rate_percent": round(iteration_hot_hits / len(hot_queries) * 100, 2),
                        "hot_hits": iteration_hot_hits,
                        "hot_requests": len(hot_queries),
                        "cumulative_hits": bpm.hits,
                        "cumulative_misses": bpm.misses,
                    })

                elapsed_ms = (time.perf_counter() - started) * 1000
                total_requests = bpm.hits + bpm.misses
                results[policy] = {
                    "hits": bpm.hits,
                    "misses": bpm.misses,
                    "hit_rate": bpm.get_hit_rate() * 100,
                    "hot_hit_rate": hot_hits / hot_requests * 100,
                    "elapsed_ms": elapsed_ms,
                }
        finally:
            disk_manager.close()
    return results, timeline


def run_benchmark_suite(
    scenarios: tuple[dict, ...] = DEFAULT_SCENARIOS,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    num_iterations: int = 20,
    hot_queries_per_iteration: int = 10,
    output_directory: str | Path = ".",
) -> tuple[list[dict], list[dict]]:
    """Sweep workloads and seeds; write raw timelines and per-workload summaries."""
    if not scenarios or not seeds:
        raise ValueError("at least one scenario and one seed are required")
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    raw_timeline: list[dict] = []
    samples: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for scenario in scenarios:
        for seed in seeds:
            results, timeline = run_cache_pollution_experiment(
                pool_size=scenario["pool_size"],
                num_hot_pages=scenario["num_hot_pages"],
                num_cold_pages=scenario["num_cold_pages"],
                num_iterations=num_iterations,
                hot_queries_per_iteration=hot_queries_per_iteration,
                seed=seed,
            )
            for row in timeline:
                raw_timeline.append({"scenario": scenario["name"], "pool_size": scenario["pool_size"], "num_hot_pages": scenario["num_hot_pages"], "num_cold_pages": scenario["num_cold_pages"], **row})
            for policy, result in results.items():
                samples[(scenario["name"], policy)].append(result)

    summary: list[dict] = []
    for scenario in scenarios:
        for policy in ("lru", "clock"):
            group = samples[(scenario["name"], policy)]
            summary.append({
                "scenario": scenario["name"],
                "policy": policy.upper(),
                "pool_size": scenario["pool_size"],
                "hot_pages": scenario["num_hot_pages"],
                "cold_pages": scenario["num_cold_pages"],
                "seeds": len(seeds),
                "mean_overall_hit_rate_percent": round(statistics.mean(x["hit_rate"] for x in group), 2),
                "stdev_overall_hit_rate_percent": round(statistics.stdev(x["hit_rate"] for x in group), 2) if len(group) > 1 else 0.0,
                "mean_hot_hit_rate_percent": round(statistics.mean(x["hot_hit_rate"] for x in group), 2),
                "stdev_hot_hit_rate_percent": round(statistics.stdev(x["hot_hit_rate"] for x in group), 2) if len(group) > 1 else 0.0,
                "mean_hits": round(statistics.mean(x["hits"] for x in group), 2),
                "mean_misses": round(statistics.mean(x["misses"] for x in group), 2),
            })

    timeline_fields = ["scenario", "pool_size", "num_hot_pages", "num_cold_pages", "policy", "seed", "iteration", "hot_hit_rate_percent", "hot_hits", "hot_requests", "cumulative_hits", "cumulative_misses"]
    summary_fields = list(summary[0])
    for filename, rows, fields in (
        ("cache_pollution_timeline.csv", raw_timeline, timeline_fields),
        ("cache_pollution_summary.csv", summary, summary_fields),
    ):
        with (output_directory / filename).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    print("Workload averages across fixed seeds (mean ± sample SD):")
    print("Scenario         Policy   Overall hit rate      Hot-query hit rate")
    for row in summary:
        print(
            f"{row['scenario']:<16} {row['policy']:<7} "
            f"{row['mean_overall_hit_rate_percent']:>6.2f} ± {row['stdev_overall_hit_rate_percent']:<6.2f}% "
            f"{row['mean_hot_hit_rate_percent']:>6.2f} ± {row['stdev_hot_hit_rate_percent']:<6.2f}%"
        )
    print(f"Detailed iterations: {output_directory / 'cache_pollution_timeline.csv'}")
    print(f"Summary table:       {output_directory / 'cache_pollution_summary.csv'}")
    return summary, raw_timeline


if __name__ == "__main__":
    run_benchmark_suite(output_directory=Path.cwd())
