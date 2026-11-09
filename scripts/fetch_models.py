from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicetype import models  # noqa: E402


def parse_models(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(models.MODEL_ORDER)
    keys = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [key for key in keys if key not in models.MODELS]
    if unknown:
        raise SystemExit(f"Unknown models: {', '.join(unknown)}")
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Download VoiceType speech models")
    parser.add_argument(
        "--models",
        default="small",
        help="Comma-separated model keys or 'all' (base,small,medium,large-v3-turbo)",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "models"),
        help="Target directory (default: <repo>/models)",
    )
    args = parser.parse_args()

    output = Path(args.output)
    for key in parse_models(args.models):
        info = models.MODELS[key]
        target = output / key
        print(f"{info.display_name}: {info.repo_id}")
        state = {"last": -1}

        def progress(done: int, total: int) -> None:
            if total > 0:
                percent = int(done * 100 / total)
                if percent == state["last"]:
                    return
                state["last"] = percent
                print(
                    f"\r  {done / (1024 * 1024):.0f} / {total / (1024 * 1024):.0f} MB "
                    f"({percent}%)",
                    end="",
                    flush=True,
                )
            else:
                print(
                    f"\r  {done / (1024 * 1024):.0f} MB",
                    end="",
                    flush=True,
                )

        try:
            models.download_model(key, target_dir=target, progress=progress)
        except Exception as exc:
            print(f"\n  failed: {exc}")
            return 1
        print(f"\n  saved to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
