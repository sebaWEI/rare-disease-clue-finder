#!/usr/bin/env python3
"""
build_vector_index.py — Phase 6: Offline NLP Vector Search Index
==================================================================
Builds a FAISS index over all HPO phenotype terms using
sentence-transformers (all-MiniLM-L6-v2, 384-dim embeddings).

Prerequisite: model must be pre-downloaded to HF cache.
  python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

Outputs
-------
  hpo_index.faiss   — FAISS IndexFlatIP index (cosine similarity)
  hpo_mapping.pkl   — list of {"hpo_id": str, "name": str}

Usage
-----
  python3 build_vector_index.py
"""

import argparse
import csv
import os
import pickle
import sys
import time
from typing import Dict, List

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 256


def load_hpo_texts(nodes_csv: str) -> List[Dict[str, str]]:
    """Read nodes.csv and build text representations for HPO terms only."""
    entries: List[Dict[str, str]] = []
    print(f"📖 Reading HPO nodes from {nodes_csv} ...")

    with open(nodes_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("type", "").strip() != "Phenotype":
                continue

            hpo_id = row["id"].strip()
            name = row.get("name", "").strip() or hpo_id
            synonyms = row.get("synonyms", "").strip()

            parts = [name]
            if synonyms:
                parts.extend(s.strip() for s in synonyms.split("|") if s.strip())
            text = " ; ".join(parts)

            entries.append({"hpo_id": hpo_id, "name": name, "text": text})

    print(f"   ✓  {len(entries):,} phenotype terms loaded")
    return entries


def build_index(entries: List[Dict[str, str]], output_dir: str) -> None:
    """Embed all HPO texts with sentence-transformers, build FAISS index."""

    texts = [e["text"] for e in entries]

    # --- Load model (offline — must be pre-downloaded) ---
    print(f"\n🧠 Loading model: {MODEL_NAME} (offline mode) ...")
    t0 = time.time()
    os.environ["HF_HUB_OFFLINE"] = "1"
    model = SentenceTransformer(MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    print(f"   ✓  Loaded in {time.time() - t0:.1f}s  (dim={dim})")

    # --- Generate embeddings ---
    print(f"\n🔢 Generating embeddings for {len(texts):,} texts ...")
    t0 = time.time()
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,  # L2-normalised → cosine via inner product
    ).astype(np.float32)
    print(f"   ✓  {embeddings.shape[0]:,} vectors of dim {dim} "
          f"in {time.time() - t0:.1f}s")

    # --- Build FAISS index (inner product = cosine on normalized vectors) ---
    print(f"\n📦 Building FAISS IndexFlatIP (dim={dim}) ...")
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    print(f"   ✓  Index contains {index.ntotal:,} vectors")

    # --- Save artifacts ---
    index_path = os.path.join(output_dir, "hpo_index.faiss")
    mapping_path = os.path.join(output_dir, "hpo_mapping.pkl")

    print(f"\n💾 Saving FAISS index → {index_path}")
    faiss.write_index(index, index_path)

    mapping = [{"hpo_id": e["hpo_id"], "name": e["name"]} for e in entries]
    print(f"💾 Saving mapping ({len(mapping):,} entries) → {mapping_path}")
    with open(mapping_path, "wb") as f:
        pickle.dump(mapping, f)

    # --- Summary ---
    size_mb = os.path.getsize(index_path) / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f" ✅  Vector index built!")
    print(f"     Model:    {MODEL_NAME}")
    print(f"     Vectors:  {len(entries):,}")
    print(f"     Dim:      {dim}")
    print(f"     Index:    {index_path}  ({size_mb:.1f} MB)")
    print(f"     Mapping:  {mapping_path}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Build FAISS vector index for HPO semantic search"
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory containing nodes.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (defaults to --data-dir)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir if args.output_dir else data_dir
    nodes_csv = os.path.join(data_dir, "nodes.csv")

    if not os.path.isfile(nodes_csv):
        print(f"❌  nodes.csv not found at {nodes_csv}", file=sys.stderr)
        sys.exit(1)

    entries = load_hpo_texts(nodes_csv)
    build_index(entries, output_dir)


if __name__ == "__main__":
    main()
