#!/usr/bin/env python3.9
"""One-time script to build/rebuild the ChromaDB recipe embedding index."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.semantic_search import index_all_recipes, get_indexed_count

if __name__ == "__main__":
    print("Building recipe embeddings (this may take a minute on first run)…")
    n = index_all_recipes()
    print(f"✅ Indexed {n} recipes. Collection now has {get_indexed_count()} entries.")
