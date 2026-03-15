from __future__ import annotations
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from model import Entity, World


try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore


# ============================================================
# Optional semantic embedding support
# ============================================================

def cosine(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = 0.0
    na = 0.0
    nb = 0.0

    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y

    if na == 0.0 or nb == 0.0:
        return 0.0

    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class Embedder:
    """
    Wrapper around SentenceTransformer that only loads from a local model directory.

    If the model folder does not exist, or sentence-transformers is not installed,
    semantic parsing is disabled and the engine falls back to symbolic parsing.
    """
    local_model_dir: str = "./models/all-MiniLM-L6-v2"
    model: Optional[object] = None
    load_error: Optional[str] = None

    def __post_init__(self) -> None:
        model_path = Path(self.local_model_dir)

        if SentenceTransformer is None:
            self.load_error = "sentence-transformers is not installed."
            self.model = None
            return

        if not model_path.exists():
            self.load_error = f"Local model directory not found: {model_path.resolve()}"
            self.model = None
            return

        try:
            self.model = SentenceTransformer(
                str(model_path),
                local_files_only=True,
            )
        except Exception as exc:
            self.load_error = f"Failed to load local model: {exc}"
            self.model = None

    def enabled(self) -> bool:
        """Return True if semantic embeddings are available."""
        return self.model is not None

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings. If unavailable, return trivial vectors so the rest
        of the system can continue running in symbolic-only mode.
        """
        if not self.enabled():
            return [[0.0] for _ in texts]

        vecs = self.model.encode(texts, normalize_embeddings=False)
        return [v.tolist() for v in vecs]


@dataclass
class SemanticIntentRouter:
    """
    Route an input utterance to one or more verb definitions using embeddings.

    templates maps:
        verb_id -> list of semantic phrases/examples
    """
    embedder: Embedder
    templates: Dict[str, List[str]]
    template_vecs: Dict[str, List[List[float]]] = field(default_factory=dict)

    def build(self) -> None:
        """Precompute embeddings for all template phrases."""
        if not self.embedder.enabled():
            return

        for verb_id, phrases in self.templates.items():
            self.template_vecs[verb_id] = self.embedder.embed(phrases)

    def route(self, user_text: str) -> List[Tuple[str, float]]:
        """
        Return ranked (verb_id, similarity_score).
        """
        if not self.embedder.enabled():
            return []

        u_vec = self.embedder.embed([user_text])[0]
        scored: List[Tuple[str, float]] = []

        for verb_id, vecs in self.template_vecs.items():
            best = 0.0
            for vec in vecs:
                best = max(best, cosine(u_vec, vec))
            scored.append((verb_id, best))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ============================================================
# Semantic entity indexing
# ============================================================

def entity_surface_text(ent: Entity) -> str:
    """Build a semantic text profile for an entity."""
    bits: List[str] = [ent.name]
    bits.extend(ent.aliases)
    bits.extend(sorted(list(ent.tags)))

    desc = ent.props.get("desc")
    if isinstance(desc, str):
        bits.append(desc)

    return " ".join(bits).lower()


@dataclass
class SemanticEntityIndex:
    """
    Embedding index for visible entities.
    """
    embedder: Embedder
    eid_to_vec: Dict[str, List[float]] = field(default_factory=dict)
    eid_to_text: Dict[str, str] = field(default_factory=dict)

    def rebuild_for_visible(self, world: World) -> None:
        """Rebuild the visible-entity embedding index."""
        self.eid_to_vec.clear()
        self.eid_to_text.clear()

        if not self.embedder.enabled():
            return

        visible = world.visible_entities()
        texts = []

        for eid in visible:
            text = entity_surface_text(world.entity(eid))
            self.eid_to_text[eid] = text
            texts.append(text)

        vecs = self.embedder.embed(texts)

        for eid, vec in zip(visible, vecs):
            self.eid_to_vec[eid] = vec

    def match(self, phrase: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """Return top semantic entity matches."""
        if not self.embedder.enabled():
            return []

        q_vec = self.embedder.embed([phrase.lower()])[0]
        scored: List[Tuple[str, float]] = []

        for eid, vec in self.eid_to_vec.items():
            scored.append((eid, cosine(q_vec, vec)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]