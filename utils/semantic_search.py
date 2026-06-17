"""Phase 6: Semantic recipe search via sentence-transformers + ChromaDB."""
from __future__ import annotations

import json
import os
from typing import Optional

_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_CHROMA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "chroma")
_COLLECTION  = "recipes"

# Lazy singletons
_embedder = None
_client   = None
_col      = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(_MODEL_NAME)
    return _embedder


def _get_collection():
    global _client, _col
    if _col is None:
        import chromadb
        _client = chromadb.PersistentClient(path=_CHROMA_DIR)
        _col    = _client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _col


def _recipe_text(recipe: dict, ingredients: list[dict]) -> str:
    """Build a single text string to embed for a recipe."""
    parts = [recipe.get("name", "")]
    cats  = recipe.get("category") or []
    if isinstance(cats, list):
        parts.extend(cats)
    methods = recipe.get("cooking_method") or []
    if isinstance(methods, list):
        parts.extend(methods)
    tags = recipe.get("tags") or []
    if isinstance(tags, list):
        parts.extend(tags)
    for ing in ingredients:
        parts.append(ing.get("name", ""))
    notes = recipe.get("notes") or ""
    if notes:
        parts.append(notes)
    steps = recipe.get("steps") or []
    if isinstance(steps, list) and steps:
        # Include first step only for brevity
        parts.append(str(steps[0]))
    return " ".join(p for p in parts if p)


def index_recipe(recipe: dict, ingredients: list[dict]) -> None:
    """Upsert a single recipe into the ChromaDB collection."""
    col  = _get_collection()
    emb  = _get_embedder()
    rid  = recipe["id"]
    text = _recipe_text(recipe, ingredients)
    vec  = emb.encode(text, normalize_embeddings=True).tolist()
    col.upsert(
        ids=[rid],
        embeddings=[vec],
        documents=[text],
        metadatas=[{"name": recipe.get("name", "")}],
    )


def index_all_recipes() -> int:
    """Index every recipe in the DB. Returns number indexed."""
    from db.recipes import get_all_recipes, get_ingredients
    recipes = get_all_recipes()
    col     = _get_collection()
    emb     = _get_embedder()
    if not recipes:
        return 0
    ids, vecs, docs, metas = [], [], [], []
    for r in recipes:
        ings = get_ingredients(r["id"])
        text = _recipe_text(r, ings)
        vec  = emb.encode(text, normalize_embeddings=True).tolist()
        ids.append(r["id"])
        vecs.append(vec)
        docs.append(text)
        metas.append({"name": r.get("name", "")})
    col.upsert(ids=ids, embeddings=vecs, documents=docs, metadatas=metas)
    return len(ids)


def semantic_search(query: str, top_k: int = 5) -> list[dict]:
    """Return up to top_k recipe dicts ranked by semantic similarity.

    Each result: {"id": ..., "name": ..., "score": float (0–1)}
    """
    col = _get_collection()
    if col.count() == 0:
        return []
    emb  = _get_embedder()
    vec  = emb.encode(query, normalize_embeddings=True).tolist()
    res  = col.query(query_embeddings=[vec], n_results=min(top_k, col.count()))
    out  = []
    ids_list   = res["ids"][0]
    metas_list = res["metadatas"][0]
    dists_list = res["distances"][0]
    for rid, meta, dist in zip(ids_list, metas_list, dists_list):
        out.append({
            "id":    rid,
            "name":  meta.get("name", ""),
            "score": round(1.0 - dist, 3),  # cosine similarity
        })
    return out


def get_indexed_count() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0


def delete_recipe(recipe_id: str) -> None:
    try:
        _get_collection().delete(ids=[recipe_id])
    except Exception:
        pass
