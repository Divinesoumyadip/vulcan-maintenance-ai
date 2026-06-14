"""Generate a domain-specific fine-tuning dataset for a maintenance SLM.

The problem statement grants additional merit for a domain-specific model.
This script uses the Anthropic API (your key, ~$1-3 for 300 examples) to
synthesize instruction-tuning pairs for a SMALL model that handles VULCAN's
A1 sub-task: classifying maintenance inputs and extracting entities. The
big model stays the reasoner; the fine-tuned SLM becomes a fast, cheap
front-end classifier — a defensible bimodal design matching Tata Steel's
own stated Narrow AI + Agentic AI strategy.

Output: finetune/dataset.jsonl  (chat format: {"messages":[...]})
Usage:  ANTHROPIC_API_KEY=... python finetune/generate_dataset.py --n 300
Then train per finetune/README_FINETUNE.md (LoRA on an open SLM).
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEED_EQUIPMENT = [
    ("CC2-MO-01", "mold oscillator", "CASTING"),
    ("HSM-COILER-01", "downcoiler", "ROLLING"),
    ("UTIL-HPU-03", "hydraulic power unit", "UTILITIES"),
    ("BF2-BLW-01", "blast furnace blower", "BLAST FURNACE"),
    ("RHF-01", "reheat furnace", "ROLLING"),
    ("CC1-LC-02", "ladle crane", "CASTING"),
    ("HSM-F3", "finishing stand F3", "ROLLING"),
    ("CT-PMP-04", "cooling tower pump", "UTILITIES"),
]
INPUT_TYPES = ["SENSOR DUMP", "FAULT CODE", "NL QUERY", "MAINTENANCE LOG",
               "ALARM REPORT", "INSPECTION REPORT", "DELAY LOG",
               "SPARES DATA", "SCENARIO PROMPT"]
LANGUAGES = ["English", "English", "English", "Hindi", "Hinglish"]

SLM_SYSTEM = ("You are a maintenance-input classifier for a steel plant. "
              "Given a raw input, respond ONLY with JSON: "
              '{"input_types": [...], "equipment_ids": [...], '
              '"parameters": [...], "urgency_signals": [...], '
              '"language": "...", "completeness_0_100": int}')

GEN_PROMPT = """Generate ONE realistic steel-plant maintenance input and its
ground-truth classification for training a small classifier model.

Constraints:
- Input type(s) to express: {itypes}
- Equipment: {eq_id} ({eq_name}, {chain} chain)
- Language of the input text: {lang}
- Make values physically plausible for steel plants; vary phrasing,
  formality, typos occasionally (real engineers type fast).

Respond ONLY with JSON:
{{"raw_input": "<the input text an engineer/system would produce>",
 "label": {{"input_types": [...], "equipment_ids": ["{eq_id}"],
  "parameters": [...], "urgency_signals": [...],
  "language": "<English|Hindi|Hinglish>", "completeness_0_100": <int>}}}}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--model", default=os.environ.get(
        "ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first.")
        return 2
    import anthropic
    client = anthropic.Anthropic()

    out_path = Path(__file__).parent / "dataset.jsonl"
    rng = random.Random(7)
    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        while written < args.n:
            eq = rng.choice(SEED_EQUIPMENT)
            itypes = rng.sample(INPUT_TYPES, k=rng.choice([1, 1, 1, 2]))
            prompt = GEN_PROMPT.format(itypes=itypes, eq_id=eq[0],
                                       eq_name=eq[1], chain=eq[2],
                                       lang=rng.choice(LANGUAGES))
            try:
                resp = client.messages.create(
                    model=args.model, max_tokens=800,
                    messages=[{"role": "user", "content": prompt}])
                text = "".join(b.text for b in resp.content
                               if b.type == "text")
                start, end = text.find("{"), text.rfind("}")
                ex = json.loads(text[start:end + 1])
                record = {"messages": [
                    {"role": "system", "content": SLM_SYSTEM},
                    {"role": "user", "content": ex["raw_input"]},
                    {"role": "assistant",
                     "content": json.dumps(ex["label"],
                                           ensure_ascii=False)},
                ]}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                if written % 25 == 0:
                    print(f"{written}/{args.n} examples")
            except Exception as exc:
                print(f"skip (gen error): {exc}")
    print(f"Done: {written} examples → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
