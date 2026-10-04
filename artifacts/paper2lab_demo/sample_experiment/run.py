"""Deterministic Paper2Lab smoke run; replace TODOs with real code."""
from __future__ import annotations
import argparse, json, random, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--config", default=str(ROOT / "config.yaml"))
parser.add_argument("--output", default=str(ROOT / "results" / "results.json"))
parser.add_argument("--seed", type=int, default=None)
args = parser.parse_args()
config = json.loads(Path(args.config).read_text(encoding="utf-8"))
seed = int(args.seed if args.seed is not None else config.get("seed", 42))
rng = random.Random(seed)
started = time.time()
# TODO: implement the paper-specific dataset, model, and training procedure.
metric_names = list(config.get("metrics", ["accuracy"]))
metrics = {str(metric): round(0.5 + rng.random() * 0.1, 4) for metric in metric_names}
results = {
    "status": "mock_skeleton",
    "mode": "mock",
    "metrics": metrics,
    "todo": config.get("todo", []),
    "seed": seed,
    "runtime_seconds": round(time.time() - started, 6),
}
out = Path(args.output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
