from pathlib import Path


def load_prompt(prompt_name: str) -> str:
    base_dir = Path(__file__).resolve().parent.parent
    prompt_path = base_dir / "prompts" / prompt_name

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8").strip()
