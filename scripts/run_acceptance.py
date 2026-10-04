"""Run and record the Paper2Lab acceptance suite."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def command(args: list[str]) -> dict[str, object]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    return {"command": args, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = [
        command([sys.executable, "-m", "pytest", "-q"]),
        command([sys.executable, "-m", "paper2lab", "--self-test"]),
        command([sys.executable, "-m", "paper2lab", "--service-self-test"]),
        command([sys.executable, "scripts/run_demo.py", "--workspace", "artifacts/paper2lab_demo", "--fresh"]),
        command([sys.executable, "scripts/run_demo.py", "--workspace", "artifacts/paper2lab_demo_compat", "--fresh", "--compat"]),
        command([sys.executable, "-m", "py_compile", *[str(path) for path in sorted((ROOT / "paper2lab").glob("*.py"))]]),
    ]
    passed = all(item["returncode"] == 0 for item in results)
    payload = {
        "status": "PASS" if passed else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "paid_api_calls": 0,
        "checks": results,
    }
    json_path = ARTIFACTS / "paper2lab_test_report.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Paper2Lab Acceptance Test Report",
        "",
        f"Status: **{payload['status']}**",
        f"Generated: `{payload['generated_at']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for item in results:
        label = " ".join(str(part) for part in item["command"])
        result = "PASS" if item["returncode"] == 0 else f"FAIL ({item['returncode']})"
        lines.append(f"| `{label}` | {result} |")
    lines += [
        "",
        "## Coverage",
        "- PDF/Markdown/TXT/HTML import parser",
        "- Section recognition and manual rename API",
        "- RuleBasedExtractor and MockLLMProvider",
        "- Claim → Blueprint → skeleton generation",
        "- Mock execution, comparison, reproducibility checklist",
        "- Async subprocess Run/Pause/Resume/Cancel/Retry lifecycle and audit persistence",
        "- Editable metadata/section/Claim/Blueprint/formula records and result provenance graph",
        "- Markdown + HTML reproduction report",
        "- Save/close/reopen round trip",
        "- Tkinter UI import-safe compilation",
        "",
        "No external network service or paid LLM API is used.",
        "",
    ]
    (ARTIFACTS / "paper2lab_test_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "json": str(json_path), "markdown": str(ARTIFACTS / "paper2lab_test_report.md")}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
