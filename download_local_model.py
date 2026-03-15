"""
One-time model download script.

Run this while you are online. It downloads the embedding model to a local folder
that your interactive fiction game will use later in offline mode.

Usage:
    python download_local_model.py
"""

from pathlib import Path
from sentence_transformers import SentenceTransformer


def main() -> None:
    # Choose a local directory for storing the model files.
    local_model_dir = Path("./models/all-MiniLM-L6-v2")
    local_model_dir.mkdir(parents=True, exist_ok=True)

    # This model name is downloaded from Hugging Face the first time.
    model_name = "sentence-transformers/all-MiniLM-L6-v2"

    print(f"Downloading model: {model_name}")
    print(f"Saving to: {local_model_dir.resolve()}")

    # Load from hub (online) and save locally.
    model = SentenceTransformer(model_name)
    model.save(str(local_model_dir))

    print("Done.")
    print("You can now run the game in offline mode using the saved local model.")


if __name__ == "__main__":
    main()