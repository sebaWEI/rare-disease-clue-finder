#!/usr/bin/env python3
"""
build_vector_index.py — Phase 6: Cloud-based Embedding for FAISS
==================================================================
Builds a FAISS index over all HPO phenotype terms using
cloud API embeddings (BAAI/bge-m3, 1024-dim) via SiliconFlow.

Prerequisite: set EMBEDDING_API_KEY env var.
  export EMBEDDING_API_KEY="sk-your-siliconflow-key"

Outputs
-------
  hpo_index.faiss   — FAISS IndexFlatIP index (cosine similarity, 1024-dim)
  hpo_mapping.pkl   — list of {"hpo_id": str, "name": str}

Usage
-----
  python3 build_vector_index.py
  python3 build_vector_index.py --data-dir /path/to/data --batch-size 32
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
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "sk-your-embedding-key")
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")

# bge-m3 dimensions
EMBEDDING_DIM = 1024
BATCH_SIZE = 32  # API supports batch input; tune based on rate limits


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
            definition = row.get("definition", "").strip()

            parts = [f"Name: {name}"]
            if synonyms:
                parts.append(f"Synonyms: {synonyms}")
            if definition:
                # Truncate very long definitions to avoid embedding dilution
                defn_short = definition[:500]
                parts.append(f"Definition: {defn_short}")
            text = " | ".join(parts)

            entries.append({"hpo_id": hpo_id, "name": name, "text": text})

    print(f"   ✓  {len(entries):,} phenotype terms loaded")
    return entries


def build_index(entries: List[Dict[str, str]], output_dir: str,
                batch_size: int = BATCH_SIZE) -> None:
    """Embed all HPO texts via cloud API and build FAISS IndexFlatIP."""

    texts = [e["text"] for e in entries]
    total = len(texts)

    # --- Init API client ---
    print(f"\n☁️  Connecting to Embedding API: {EMBEDDING_BASE_URL}")
    print(f"   Model: {EMBEDDING_MODEL} (dim={EMBEDDING_DIM})")
    client = OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_BASE_URL,
    )

    # --- Generate embeddings in batches ---
    print(f"\n🔢 Generating embeddings for {total:,} texts "
          f"(batch_size={batch_size}) ...")
    t0 = time.time()

    all_embeddings: List[np.ndarray] = []
    n_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        batch_texts = texts[i:i + batch_size]
        batch_num = i // batch_size + 1

        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch_texts,
            )
            # Sort by index to preserve order
            batch_embeddings = sorted(response.data, key=lambda x: x.index)
            for emb in batch_embeddings:
                vec = np.array(emb.embedding, dtype=np.float32)
                # L2-normalize for cosine similarity via inner product
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                all_embeddings.append(vec)

            elapsed = time.time() - t0
            rate = (i + len(batch_texts)) / elapsed
            print(f"   [{batch_num}/{n_batches}] {i + len(batch_texts):,}/{total:,} "
                  f"({rate:.0f} texts/s, {elapsed:.0f}s elapsed)", end="\r")
        except Exception as e:
            print(f"\n   ❌  Batch {batch_num} failed: {e}")
            raise

    print()  # newline after progress

    # Stack into matrix
    embeddings = np.vstack(all_embeddings).astype(np.float32)
    print(f"   ✓  {embeddings.shape[0]:,} vectors of dim {embeddings.shape[1]} "
          f"in {time.time() - t0:.1f}s")

    # --- Build FAISS index (inner product on L2-normalized vectors) ---
    print(f"\n📦 Building FAISS IndexFlatIP (dim={EMBEDDING_DIM}) ...")
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
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
    print(f"     Model:    {EMBEDDING_MODEL}")
    print(f"     Vectors:  {len(entries):,}")
    print(f"     Dim:      {EMBEDDING_DIM}")
    print(f"     Index:    {index_path}  ({size_mb:.1f} MB)")
    print(f"     Mapping:  {mapping_path}")
    print(f"     API cost: {total} embedding calls ({n_batches} batches)")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Build FAISS vector index for HPO semantic search (cloud API)"
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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of texts per API batch (default: {BATCH_SIZE})",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir if args.output_dir else data_dir
    nodes_csv = os.path.join(data_dir, "nodes.csv")

    if not os.path.isfile(nodes_csv):
        print(f"❌  nodes.csv not found at {nodes_csv}", file=sys.stderr)
        sys.exit(1)

    entries = load_hpo_texts(nodes_csv)
    build_index(entries, output_dir, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
