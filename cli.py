"""VULCAN CLI — terminal chat for quick demos.  Run:  python cli.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vulcan.orchestrator import VulcanOrchestrator  # noqa: E402


def main() -> None:
    print("🔥 VULCAN — Agentic Maintenance Intelligence Core (CLI)")
    print("Type your maintenance query. 'exit' to quit.\n")
    orch = VulcanOrchestrator()
    while True:
        try:
            q = input("engineer> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"exit", "quit"}:
            break
        n = len(orch.tool_trace)
        print("\n" + orch.ask(q) + "\n")
        for c in orch.tool_trace[n:]:
            print(f"  [tool] {c['tool']}({c['input']}) → "
                  f"{c['output_status']}")
        print()


if __name__ == "__main__":
    main()
