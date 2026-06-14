# Domain-Specific SLM Fine-Tuning (optional merit item)

Bimodal design matching Tata Steel's stated AI strategy: a fine-tuned
**small** model handles fast, cheap input classification (VULCAN's A1
sub-task); the large model stays the reasoner/orchestrator.

## Step 1 — Generate the dataset (~$1-3 of API usage)
```bash
ANTHROPIC_API_KEY=sk-ant-... python finetune/generate_dataset.py --n 300
```
Produces `finetune/dataset.jsonl` (chat-format instruction pairs:
raw maintenance input → JSON classification with input types, equipment
IDs, parameters, urgency signals, language, completeness score).
Inspect ~20 examples manually and delete bad ones before training —
dataset quality matters more than size.

## Step 2 — LoRA-train an open SLM (free on Google Colab T4)
Recommended: a small instruct model (1-4B parameters, e.g. from the
Qwen/Llama/Gemma families — pick per current licenses) with Unsloth or
Hugging Face PEFT. Typical recipe: LoRA r=16, lr 2e-4, 3 epochs,
train/val split 90/10. A 300-example set trains in well under an hour.

## Step 3 — Evaluate before claiming anything
Hold out 30 examples. Report exact-match on `input_types` and
`equipment_ids`, and MAE on `completeness_0_100`, versus the zero-shot
base model. Only claim improvement you measured.

## Step 4 — (Optional) wire it in
Serve the SLM locally (e.g. llama.cpp / ollama) and call it inside
`vulcan/orchestrator.py` before the main model to pre-tag the input.
Keep the big-model path as fallback when SLM confidence is low.

## Honest framing for judges
"I fine-tuned a domain SLM for input classification — here is the
dataset, recipe, and held-out accuracy vs the base model" is a strong,
verifiable claim. Do NOT claim a fine-tuned model you haven't trained
and measured.
