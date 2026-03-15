"""
verify_local_model.py

Purpose:
- Verify that your local sentence-transformers model can be loaded fully offline
- Confirm that embeddings can be generated
- Report the local model folder size
- Print a small similarity sanity check

Expected local model directory:
    ./models/all-MiniLM-L6-v2

Run:
    python verify_local_model.py
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import List

# Force Hugging Face libraries into offline mode before import/use.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore


LOCAL_MODEL_DIR = Path("./models/all-MiniLM-L6-v2")


def cosine(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def folder_size_mb(path: Path) -> float:
    """Return total folder size in megabytes."""
    total_bytes = 0
    for p in path.rglob("*"):
        if p.is_file():
            total_bytes += p.stat().st_size
    return total_bytes / (1024 * 1024)


def main() -> None:
    print("Verifying local embedding model...")
    print()

    # Check that sentence-transformers is installed.
    if SentenceTransformer is None:
        print("FAIL: sentence-transformers is not installed.")
        print("Install it with:")
        print("    pip install sentence-transformers huggingface_hub")
        return

    # Check that the local model directory exists.
    if not LOCAL_MODEL_DIR.exists():
        print(f"FAIL: local model directory does not exist:")
        print(f"    {LOCAL_MODEL_DIR.resolve()}")
        print()
        print("You need to run the one-time download script first:")
        print("    python download_local_model.py")
        return

    print(f"Model directory found:")
    print(f"    {LOCAL_MODEL_DIR.resolve()}")
    print(f"Folder size: {folder_size_mb(LOCAL_MODEL_DIR):.2f} MB")
    print()

    # Attempt to load strictly from local files only.
    try:
        model = SentenceTransformer(
            str(LOCAL_MODEL_DIR),
            local_files_only=True,
        )
    except Exception as exc:
        print("FAIL: could not load the model in local-only mode.")
        print(f"Error: {exc}")
        return

    print("PASS: model loaded successfully in local-only mode.")
    print()

    # Generate a few test embeddings.
    test_sentences = [
        "open the wooden box",
        "unlock the oak door with the brass key",
        "take the small key",
        "look around the room",
    ]

    try:
        vectors = model.encode(test_sentences, normalize_embeddings=False)
    except Exception as exc:
        print("FAIL: model loaded, but embedding generation failed.")
        print(f"Error: {exc}")
        return

    print("PASS: embeddings generated successfully.")
    print(f"Number of test sentences: {len(test_sentences)}")
    print(f"Embedding dimension: {len(vectors[0]) if len(vectors) > 0 else 0}")
    print()

    # Small sanity check: similar commands should be more related than unrelated ones.
    # These are not strict guarantees, just a quick health check.
    s1 = "put the brass key in the wooden box"
    s2 = "place the small key into the crate"
    s3 = "go north"

    sanity_vectors = model.encode([s1, s2, s3], normalize_embeddings=False)
    v1 = sanity_vectors[0].tolist() if hasattr(sanity_vectors[0], "tolist") else list(sanity_vectors[0])
    v2 = sanity_vectors[1].tolist() if hasattr(sanity_vectors[1], "tolist") else list(sanity_vectors[1])
    v3 = sanity_vectors[2].tolist() if hasattr(sanity_vectors[2], "tolist") else list(sanity_vectors[2])

    sim_related = cosine(v1, v2)
    sim_unrelated = cosine(v1, v3)

    print("Similarity sanity check:")
    print(f"  related pair   : {sim_related:.4f}")
    print(f"  unrelated pair : {sim_unrelated:.4f}")
    print()

    if sim_related > sim_unrelated:
        print("PASS: similarity check looks sensible.")
    else:
        print("WARNING: similarity check was not as expected.")
        print("The model may still be usable, but you should test it further.")
    print()
    print("Local model verification complete.")


if __name__ == "__main__":
    main()