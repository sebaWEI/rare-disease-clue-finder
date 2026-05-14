#!/usr/bin/env python3
"""
main.py — Phase 2: Graph Construction & Backend API (FastAPI)
===============================================================
Loads the 3 Phase-1 CSV files into a NetworkX DiGraph, then exposes a
REST API that implements the True Path Rule inference algorithm for
rare-disease clue finding.

Endpoints
---------
GET  /api/health          — health-check + graph statistics
POST /api/predict         — given a list of HPO IDs, return top-5 diseases
       with explainable matched paths

Usage
-----
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import csv
import json
import math
import os
import pickle
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import faiss
import networkx as nx
import numpy as np
try:
    import spacy
except ImportError:
    spacy = None  # scispacy not installed — semantic search falls back to regex
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ""),
)
FRONTEND_INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
NODES_CSV = os.path.join(DATA_DIR, "nodes.csv")
EDGES_HPO_CSV = os.path.join(DATA_DIR, "edges_hpo.csv")
EDGES_DISEASE_HPO_CSV = os.path.join(DATA_DIR, "edges_disease_hpo.csv")
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "hpo_index.faiss")
FAISS_MAPPING_PATH = os.path.join(DATA_DIR, "hpo_mapping.pkl")
LAYERSON_DICT_PATH = os.path.join(DATA_DIR, "layperson_dict.json")
VECTOR_MODEL_NAME = "all-MiniLM-L6-v2"

# Inference multipliers (True Path Rule)
SCORE_DIRECT    = 1.0   # disease has the exact HPO term
BASE_INDIRECT   = 0.5   # base weight for child/parent matches
                         # actual = BASE_INDIRECT × exp(-n) where n = number of input HPOs
                         # This exponentially suppresses indirect matches when many
                         # symptoms are provided, preventing broad-term domination.
PARENT_MAX_HOPS = 2     # limit ancestor traversal depth

TOP_N = 5              # number of diseases to return

# Phase 10: Biomarker patterns for calibrated keyword boosting
import re as _re
BIOMARKER_PATTERNS = [
    # Enzymes (end in -ase)
    r'\b\w+ase\b',
    # Specific biological markers
    r'\b(acid\s+maltase|creatine\s+kinase|CK|GAA|GLA|GBA|IDS|IDUA)\b',
    r'\b(galactosidase|glucosidase|sulfatase|dehydrogenase|transferase)\b',
    # Laboratory/measurement terms
    r'\b(elevated|decreased|low|high|reduced|absent)\s+(circulating|serum|plasma|blood)\b',
    r'\b(enzyme|protein|gene|mutation|deficiency|activity|concentration|level)\b',
    # Chemical compounds
    r'\b(ceramide|sphingomyelin|glucocerebroside|glycogen|mucopolysaccharide)\b',
    r'\b(ganglioside|sulfatide|galactocerebroside|phytanic acid)\b',
]
BIOMARKER_RE = _re.compile('|'.join(BIOMARKER_PATTERNS), _re.IGNORECASE)


def is_biomarker_fragment(text: str) -> bool:
    """Check if a fragment contains biological/chemical marker terms."""
    return bool(BIOMARKER_RE.search(text))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PredictRequest(BaseModel):
    hpo_ids: List[str] = Field(
        ...,
        min_length=1,
        description="List of HPO IDs selected by the user (e.g. ['HP:0001250', 'HP:0001249'])",
        examples=[["HP:0001250", "HP:0001249"]],
    )


class MatchedPath(BaseModel):
    """A single explainable match between a user-provided HPO and a disease-linked HPO."""
    input_hpo_id: str
    input_hpo_name: str
    matched_hpo_id: str
    matched_hpo_name: str
    match_type: str          # "direct" | "child" | "parent"
    frequency_weight: float
    score_multiplier: float
    contribution: float      # frequency_weight × score_multiplier
    explanation: str         # human-readable string


class DiseasePrediction(BaseModel):
    disease_id: str
    disease_name: str
    total_score: float
    matched_paths: List[MatchedPath]


class SuggestedHPO(BaseModel):
    hpo_id: str
    name: str
    reason: str


class PredictResponse(BaseModel):
    query_hpo_ids: List[str]
    results: List[DiseasePrediction]
    suggested_hpos: List[SuggestedHPO] = []


class HPOSearchResult(BaseModel):
    id: str
    name: str


class HPOSearchResponse(BaseModel):
    query: str
    results: List[HPOSearchResult]
    total: int


class VectorSearchResult(BaseModel):
    hpo_id: str
    name: str
    score: float  # higher = better match (cosine similarity via TF-IDF)
    hint: str = ""  # Phase 10: parent category hint for public mode disambiguation


class VectorSearchResponse(BaseModel):
    query: str
    results: List[VectorSearchResult]


class SmartSearchGroup(BaseModel):
    fragment: str
    results: List[VectorSearchResult]

class SmartSearchResponse(BaseModel):
    query: str
    fragments: List[str]
    groups: List[SmartSearchGroup]


class HealthResponse(BaseModel):
    status: str
    graph_stats: Dict[str, Any]


# ---------------------------------------------------------------------------
# Graph Manager
# ---------------------------------------------------------------------------

class DiseaseGraph:
    """Loads CSV data into a NetworkX DiGraph and provides inference methods.

    Graph topology
    --------------
    Nodes
        Phenotype nodes: id=HP:NNNNNNN, name, type="Phenotype", synonyms
        Disease nodes:    id=ORPHA:NNNN, name, type="Disease", synonyms

    Edges
        is_a (child → parent):         HP:0000002 → HP:0001507
        disease_has_hpo (disease → hpo):  ORPHA:58 → HP:0000256  (weight=0.9)

    Important direction note
    ------------------------
    HPO "is_a" edges go from child (specific) → parent (general).  This means:
      - nx.ancestors(G, hpo)  → children  (more specific terms)
      - nx.descendants(G, hpo) → parents  (more general terms)
    The naming in the code follows *semantic* meaning, not NetworkX's
    graph-direction naming.
    """

    def __init__(self):
        self.G = nx.DiGraph()
        self._hpo_id_to_name: Dict[str, str] = {}   # fast name lookups
        self._disease_id_to_name: Dict[str, str] = {}
        self._hpo_to_diseases: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        self._disease_to_hpos: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        """Orchestrate loading of all three CSV files."""
        self._check_files()
        self._load_nodes()
        self._load_hpo_edges()
        self._load_disease_hpo_edges()
        self._build_lookups()

    def _check_files(self) -> None:
        """Verify all three input CSVs exist."""
        for label, path in [
            ("nodes", NODES_CSV),
            ("edges_hpo", EDGES_HPO_CSV),
            ("edges_disease_hpo", EDGES_DISEASE_HPO_CSV),
        ]:
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Missing {label} CSV: {path}")

    def _load_nodes(self) -> None:
        """Load all nodes (Phenotype + Disease) from nodes.csv."""
        print(f"📦 Loading nodes from {NODES_CSV} ...", flush=True)
        count = 0
        with open(NODES_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                node_id = row["id"]
                node_type = row["type"]
                name = row["name"]
                synonyms = row.get("synonyms", "")
                self.G.add_node(
                    node_id,
                    name=name,
                    type=node_type,
                    synonyms=synonyms,
                )
                if node_type == "Phenotype":
                    self._hpo_id_to_name[node_id] = name
                elif node_type == "Disease":
                    self._disease_id_to_name[node_id] = name
                count += 1
        print(f"   ✓  {count:,} nodes loaded", flush=True)

    def _load_hpo_edges(self) -> None:
        """Load HPO hierarchy edges (child → parent, is_a)."""
        print(f"📦 Loading HPO edges from {EDGES_HPO_CSV} ...", flush=True)
        count = 0
        with open(EDGES_HPO_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                child = row["child_id"]
                parent = row["parent_id"]
                edge_type = row["type"]
                self.G.add_edge(child, parent, type=edge_type)
                count += 1
        print(f"   ✓  {count:,} is_a edges loaded", flush=True)

    def _load_disease_hpo_edges(self) -> None:
        """Load Disease → HPO edges with frequency weights."""
        print(f"📦 Loading disease-HPO edges from {EDGES_DISEASE_HPO_CSV} ...", flush=True)
        count = 0
        with open(EDGES_DISEASE_HPO_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                disease_id = row["disease_id"]
                hpo_id = row["hpo_id"]
                weight = float(row["frequency_weight"])
                self.G.add_edge(
                    disease_id, hpo_id,
                    type="disease_has_hpo",
                    weight=weight,
                )
                self._hpo_to_diseases[hpo_id].append((disease_id, weight))
                self._disease_to_hpos[disease_id].append((hpo_id, weight))
                count += 1
        print(f"   ✓  {count:,} disease→HPO edges loaded", flush=True)

    def _build_lookups(self) -> None:
        """Pre-compute fast-access lookup dictionaries.

        The defaultdict-based _hpo_to_diseases / _disease_to_hpos are
        built during edge loading above (O(1) amortised per edge), so
        this method is mainly a placeholder for future index builds.
        """
        pass

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, hpo_ids: List[str]) -> List[DiseasePrediction]:
        """Run the True Path Rule inference for a set of HPO IDs.

        Algorithm (per input HPO term, per disease)
        -------------------------------------------
        1. Direct Match  (×1.0):  the disease is linked to this exact HPO.
        2. Child & Parent (×rank-decay): the disease is linked to child or
           parent terms. Within each (disease, input HPO) group, indirect
           matches are sorted by frequency_weight descending, then:
             rank 1 → 0.5, rank 2 → 0.5×e⁻¹≈0.184, rank 3 → 0.5×e⁻²≈0.068, ...
           This prevents diseases with large descendant trees from dominating
           the ranking, while still giving full weight (0.5) to the best match.

        Score(disease) = Σ [ frequency_weight × score_multiplier ]

        Parameters
        ----------
        hpo_ids : list of str
            HPO terms provided by the user (e.g. ["HP:0001250", "HP:0100022"]).

        Returns
        -------
        list of DiseasePrediction (top TOP_N, sorted by total_score desc).
        """
        # --- Validate inputs ---
        valid_hpos: List[str] = []
        for hid in hpo_ids:
            hid = hid.strip()
            if hid not in self._hpo_id_to_name:
                print(f"   ⚠  Unknown HPO: {hid} — skipped", flush=True)
                continue
            valid_hpos.append(hid)

        if not valid_hpos:
            raise ValueError("None of the provided HPO IDs were recognised.")

        # --- Accumulate scores per disease ---
        # disease_scores[disease_id] = total_score
        # disease_paths[disease_id] = list of MatchedPath
        disease_scores: Dict[str, float] = defaultdict(float)
        disease_paths: Dict[str, List[MatchedPath]] = defaultdict(list)

        for input_hpo in valid_hpos:
            input_name = self._hpo_id_to_name.get(input_hpo, input_hpo)

            # --- Step A: expand input HPO → which HPOs does it cover? ---
            # Collect matched HPOs grouped by match_type (no multiplier yet —
            # that will be assigned per-disease based on rank within the group)
            direct_targets  = [input_hpo]
            child_targets   = []
            parent_targets  = []

            # Children (more specific terms)
            try:
                for child_hpo in nx.ancestors(self.G, input_hpo):
                    if child_hpo in self._hpo_id_to_name:
                        child_targets.append(child_hpo)
            except nx.NetworkXError:
                pass

            # Parents (more general terms, up to 2 hops)
            try:
                sp_len = nx.single_source_shortest_path_length(
                    self.G, input_hpo, cutoff=PARENT_MAX_HOPS
                )
                for node, dist in sp_len.items():
                    if dist >= 1 and node in self._hpo_id_to_name:
                        parent_targets.append(node)
            except nx.NetworkXError:
                pass

            # --- Step B: group all matches by disease ---
            # disease_matches[disease_id] = [(matched_hpo, match_type,
            #                                  freq_weight, matched_name), ...]
            disease_matches: Dict[str, List[Tuple[str, str, float, str]]] = defaultdict(list)

            for match_type, hpo_list in [
                ("direct", direct_targets),
                ("child",  child_targets),
                ("parent", parent_targets),
            ]:
                for matched_hpo in hpo_list:
                    matched_name = self._hpo_id_to_name.get(matched_hpo, matched_hpo)
                    for disease_id, freq_weight in self._hpo_to_diseases.get(matched_hpo, []):
                        disease_matches[disease_id].append(
                            (matched_hpo, match_type, freq_weight, matched_name)
                        )

            # --- Step C: score each disease with rank-based indirect decay ---
            for disease_id, matches in disease_matches.items():
                # Separate direct vs indirect
                direct   = [m for m in matches if m[1] == "direct"]
                indirect = [m for m in matches if m[1] in ("child", "parent")]

                # Sort indirect by frequency_weight descending (highest freq first)
                indirect.sort(key=lambda m: m[2], reverse=True)

                # Direct matches: ×1.0
                for matched_hpo, match_type, freq_weight, matched_name in direct:
                    mult = SCORE_DIRECT
                    contribution = freq_weight * mult
                    disease_scores[disease_id] += contribution
                    disease_paths[disease_id].append(
                        MatchedPath(
                            input_hpo_id=input_hpo,
                            input_hpo_name=input_name,
                            matched_hpo_id=matched_hpo,
                            matched_hpo_name=matched_name,
                            match_type=match_type,
                            frequency_weight=freq_weight,
                            score_multiplier=mult,
                            contribution=round(contribution, 4),
                            explanation=self._build_explanation(
                                input_name, matched_name, match_type, freq_weight
                            ),
                        )
                    )

                # Indirect matches: rank-based decay
                # Rank 1 → 0.5, Rank 2 → 0.5×e⁻¹≈0.184, Rank 3 → 0.5×e⁻²≈0.068, ...
                for rank, (matched_hpo, match_type, freq_weight, matched_name) in enumerate(indirect, 1):
                    mult = BASE_INDIRECT * math.exp(-(rank - 1))
                    contribution = freq_weight * mult
                    disease_scores[disease_id] += contribution
                    disease_paths[disease_id].append(
                        MatchedPath(
                            input_hpo_id=input_hpo,
                            input_hpo_name=input_name,
                            matched_hpo_id=matched_hpo,
                            matched_hpo_name=matched_name,
                            match_type=match_type,
                            frequency_weight=freq_weight,
                            score_multiplier=round(mult, 4),
                            contribution=round(contribution, 4),
                            explanation=self._build_explanation(
                                input_name, matched_name, match_type, freq_weight
                            ),
                        )
                    )

        # --- Sort & truncate ---
        ranked = sorted(disease_scores.items(), key=lambda x: x[1], reverse=True)
        top = ranked[:TOP_N]

        results: List[DiseasePrediction] = []
        for disease_id, score in top:
            disease_name = self._disease_id_to_name.get(disease_id, disease_id)
            # Sort matched paths by contribution desc within each disease
            paths = sorted(
                disease_paths[disease_id],
                key=lambda p: p.contribution,
                reverse=True,
            )
            results.append(
                DiseasePrediction(
                    disease_id=disease_id,
                    disease_name=disease_name,
                    total_score=round(score, 4),
                    matched_paths=paths,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_explanation(
        self,
        input_name: str,
        matched_name: str,
        match_type: str,
        freq_weight: float,
    ) -> str:
        """Build a human-readable explanation string for one match."""
        templates = {
            "direct": (
                "User input '{input}' matched directly with disease symptom "
                "'{matched}' (Frequency: {freq})"
            ),
            "child": (
                "User input '{input}' matched with a more specific child symptom "
                "'{matched}' (Frequency: {freq})"
            ),
            "parent": (
                "User input '{input}' matched with a more general parent symptom "
                "'{matched}' (Frequency: {freq})"
            ),
        }
        tmpl = templates.get(match_type, templates["direct"])
        return tmpl.format(input=input_name, matched=matched_name, freq=freq_weight)

    def search_hpo(self, query: str, limit: int = 20) -> List[Tuple[str, str]]:
        """Search HPO terms by name (case-insensitive substring match).

        Returns list of (id, name) tuples, sorted by match quality.
        """
        query_lower = query.lower()
        # Collect all HPO terms whose name contains the query substring
        # and score them by how early the match occurs (lower = better).
        scored: List[Tuple[int, str, str]] = []
        for hpo_id, name in self._hpo_id_to_name.items():
            idx = name.lower().find(query_lower)
            if idx == -1:
                # Also search synonyms
                node_data = self.G.nodes.get(hpo_id, {})
                synonyms = node_data.get("synonyms", "")
                if synonyms:
                    idx = synonyms.lower().find(query_lower)
            if idx != -1:
                scored.append((idx, hpo_id, name))
        # Sort: earlier match first, shorter name first
        scored.sort(key=lambda x: (x[0], len(x[2])))
        # Prioritise exact word starts (e.g. "Seizure" before "Epileptic seizure")
        starts = [(s[1], s[2]) for s in scored
                  if s[2].lower().startswith(query_lower)]
        others = [(s[1], s[2]) for s in scored
                  if not s[2].lower().startswith(query_lower)]
        results = starts + others
        return results[:limit]

    def get_anatomy_tree(self) -> List[Dict[str, str]]:
        """Return direct children of HP:0000118 (Phenotypic abnormality).

        These represent the major anatomical/physiological system categories
        at the top of the HPO hierarchy — a foundation for an anatomy-tree UI.
        In our graph (child→parent is_a edges), children of a node are its
        predecessors (nodes that have edges TO it).
        """
        root = "HP:0000118"
        children: List[Dict[str, str]] = []
        if root not in self.G:
            return children
        for pred in self.G.predecessors(root):
            edge = self.G.get_edge_data(pred, root)
            if edge and edge.get("type") == "is_a":
                name = self._hpo_id_to_name.get(pred, pred)
                children.append({"hpo_id": pred, "name": name})
        # Sort alphabetically by name
        children.sort(key=lambda x: x["name"].lower())
        return children

    def suggest_next_hpos(
        self,
        top_disease_ids: List[str],
        input_hpo_ids: Set[str],
        limit: int = 10,
    ) -> List[SuggestedHPO]:
        """Generate 'Next Best Symptom' suggestions based on the Top 5 diseases.

        Algorithm
        ---------
        1. Collect all HPOs connected to the top diseases (via disease_has_hpo).
        2. Build a 'redundant' set — ancestors + descendants of each input HPO
           (user already covered these implicitly via the True Path Rule).
        3. Filter out redundant HPOs, already-selected HPOs, and non-phenotype nodes.
        4. Score remaining HPOs by discriminative power:
             - High frequency in few diseases = great discriminator
             - score = max_frequency × (1.0 if in 1 disease, 0.5 if in 2, 0.25 if 3+)
        5. Return top `limit`, each with a reason referencing the associated disease.
        """
        if not top_disease_ids:
            return []

        # --- 1. Collect candidate HPOs from top diseases ---
        # candidate_hpos[hpo_id] = {disease_id: max_weight}
        candidate_hpos: Dict[str, Dict[str, float]] = defaultdict(dict)
        for disease_id in top_disease_ids:
            for hpo_id, weight in self._disease_to_hpos.get(disease_id, []):
                if hpo_id not in self._hpo_id_to_name:
                    continue  # skip non-HPO nodes
                if hpo_id not in candidate_hpos:
                    candidate_hpos[hpo_id] = {}
                # Keep the max weight per disease
                current = candidate_hpos[hpo_id].get(disease_id, 0.0)
                candidate_hpos[hpo_id][disease_id] = max(current, weight)

        # --- 2. Build redundant HPO set from inputs ---
        redundant: Set[str] = set(input_hpo_ids)
        for hpo_id in input_hpo_ids:
            if hpo_id not in self.G:
                continue
            try:
                # Ancestors (children in our edge direction — more specific)
                redundant.update(nx.ancestors(self.G, hpo_id))
            except nx.NetworkXError:
                pass
            try:
                # Descendants (parents — more general)
                redundant.update(nx.descendants(self.G, hpo_id))
            except nx.NetworkXError:
                pass

        # --- 3. Filter ---
        filtered: List[Tuple[str, Dict[str, float]]] = []
        for hpo_id, disease_weights in candidate_hpos.items():
            if hpo_id in redundant:
                continue
            filtered.append((hpo_id, disease_weights))

        # --- 4. Score ---
        scored: List[Tuple[float, str, str, str]] = []  # (score, hpo_id, name, reason)
        for hpo_id, disease_weights in filtered:
            disease_count = len(disease_weights)
            max_freq = max(disease_weights.values())
            # Discriminative score: high freq in few diseases
            if disease_count == 1:
                discriminative = 1.0
            elif disease_count == 2:
                discriminative = 0.5
            else:
                discriminative = 0.25
            score = max_freq * discriminative

            # Build reason: "Associated with [Disease Name]"
            top_disease_for_hpo = max(disease_weights, key=disease_weights.get)
            disease_name = self._disease_id_to_name.get(top_disease_for_hpo, top_disease_for_hpo)
            reason = f"Associated with {disease_name}"

            name = self._hpo_id_to_name.get(hpo_id, hpo_id)
            scored.append((score, hpo_id, name, reason))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)

        # --- 5. Build response ---
        suggestions: List[SuggestedHPO] = []
        for score, hpo_id, name, reason in scored[:limit]:
            suggestions.append(SuggestedHPO(hpo_id=hpo_id, name=name, reason=reason))

        return suggestions

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics about the in-memory graph."""
        pheno_nodes = sum(
            1 for _, d in self.G.nodes(data=True) if d.get("type") == "Phenotype"
        )
        disease_nodes = sum(
            1 for _, d in self.G.nodes(data=True) if d.get("type") == "Disease"
        )
        is_a_edges = sum(
            1 for _, _, d in self.G.edges(data=True) if d.get("type") == "is_a"
        )
        dh_edges = sum(
            1 for _, _, d in self.G.edges(data=True)
            if d.get("type") == "disease_has_hpo"
        )
        return {
            "total_nodes": self.G.number_of_nodes(),
            "phenotype_nodes": pheno_nodes,
            "disease_nodes": disease_nodes,
            "total_edges": self.G.number_of_edges(),
            "hpo_is_a_edges": is_a_edges,
            "disease_hpo_edges": dh_edges,
        }


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

# ── Global graph instance (loaded at startup) ──
graph: Optional[DiseaseGraph] = None
vector_model: Optional[SentenceTransformer] = None
vector_index: Optional[Any] = None   # faiss.Index
vector_mapping: List[Dict[str, str]] = []  # [{hpo_id, name}, ...]
nlp: Optional[Any] = None                  # scispacy model
layperson_dict: Dict[str, str] = {}  # HP:XXXXXXX → layperson phrase


def resolve_name(hpo_id: str, fallback_name: str, mode: str = "expert") -> str:
    """Return the display name for an HPO term based on mode.

    - expert mode: returns the original HPO name
    - public mode: returns the layperson translation if available,
      otherwise falls back to the original name
    """
    if mode == "public" and hpo_id in layperson_dict:
        return layperson_dict[hpo_id]
    return fallback_name


def resolve_parent_hint(hpo_id: str) -> str:
    """Return a short parent-category hint for an HPO term.

    Walks up the HPO is_a hierarchy (child→parent edges) to find the
    top-level system category under HP:0000118 (Phenotypic abnormality).
    Returns the category name or empty string.
    """
    if graph is None:
        return ""
    if hpo_id not in graph.G:
        return ""
    # Walk up from this HPO to find the direct child of HP:0000118
    root = "HP:0000118"
    try:
        # Get all simple paths to root (should be short in a tree-like graph)
        paths = list(nx.all_simple_paths(graph.G, hpo_id, root, cutoff=12))
        if paths:
            # The second-to-last node is the direct child of root
            path = paths[0]
            if len(path) >= 2:
                category_id = path[-2]
                return graph._hpo_id_to_name.get(category_id, "")
    except (nx.NetworkXError, nx.NodeNotFound):
        pass
    return ""


def extract_symptom_fragments(text: str) -> List[str]:
    """Use SciSpacy to extract symptom fragments from a free-text description.

    Strategy:
    1. Pre-split by top-level conjunctions (and, also, commas) to isolate
       independent symptom mentions.
    2. For each sub-sentence, extract named entities (doc.ents) and noun
       chunks (doc.noun_chunks).
    3. Clean: strip leading determiners/pronouns, deduplicate.
    4. Falls back to regex split if the NLP model is unavailable.
    """
    if nlp is None:
        import re
        return [p.strip() for p in re.split(
            r'[,;.，；。、\n]+|\band\b|\balso\b|和|还有|以及|\n',
            text, flags=re.IGNORECASE
        ) if len(p.strip()) >= 3]

    # 1. Pre-split by top-level conjunctions to isolate independent symptom clauses
    import re
    sub_sentences = [s.strip() for s in re.split(
        r'\band\b|\balso\b|,\s*(?!\d)|;\s*',
        text, flags=re.IGNORECASE
    ) if len(s.strip()) >= 3]

    if len(sub_sentences) == 1 and len(sub_sentences[0]) < 5:
        # Very short input — use as-is
        return [sub_sentences[0]]

    # 2. For each sub-sentence, extract entities + noun chunks
    cleaned: List[str] = []
    seen_clean: Set[str] = set()
    pronoun_filter = {'i', 'me', 'my', 'we', 'us', 'he', 'she', 'it', 'they', 'you'}

    for sub in sub_sentences:
        doc = nlp(sub)

        # Collect candidates: entities first (biomedical terms), then noun chunks
        candidates: List[str] = []
        seen: Set[str] = set()

        for ent in doc.ents:
            t = ent.text.strip().lower()
            if len(t) >= 2 and t not in seen and t not in pronoun_filter:
                seen.add(t)
                candidates.append(ent.text.strip())

        for chunk in doc.noun_chunks:
            t = chunk.text.strip().lower()
            if len(t) >= 3 and t not in seen and t not in pronoun_filter:
                seen.add(t)
                candidates.append(chunk.text.strip())

        # 3. Clean each candidate: strip leading determiners/pronouns
        for c in candidates:
            clean = c.lower()
            for prefix in ('a ', 'an ', 'the ', 'my ', 'his ', 'her ',
                           'their ', 'our ', 'some ', 'any ', 'i have ',
                           'i have a ', 'i feel ', 'i am ', 'i\'m '):
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
                    break
            clean = clean.strip()
            if len(clean) >= 2 and clean not in seen_clean and clean not in pronoun_filter:
                seen_clean.add(clean)
                cleaned.append(clean)

    if not cleaned:
        return [text.strip()]

    # 4. Post-process: remove substrings (keep longer version),
    #    and filter bare adjectives (e.g. "severe" alone is not a symptom)
    cleaned.sort(key=len, reverse=True)  # longest first for substring dedup
    final: List[str] = []
    for c in cleaned:
        # Check if this fragment is already covered by a longer one
        if any(c in other and c != other for other in final):
            continue
        final.append(c)

    return final


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load knowledge graph, NLP model, and FAISS index on startup.
    
    When running via python3 main.py, models are pre-loaded by _load_all_models()
    before uvicorn starts. This lifespan handles the python3 -m uvicorn main:app
    case where models must be loaded inside the ASGI lifecycle.
    """
    global graph, vector_model, vector_index, vector_mapping

    # Skip if already loaded (e.g. by _load_all_models in __main__)
    if graph is not None:
        print("   ℹ  Models pre-loaded — skipping lifespan load\n")
        yield
        return

    print("\n" + "=" * 60)
    print(" 🧬  Explainable Rare Disease Clue Finder — Starting up")
    print("=" * 60)

    # 1. Load knowledge graph
    try:
        graph = DiseaseGraph()
        graph.load_all()
        stats = graph.stats()
        print(f"\n✅  Graph ready: {stats['total_nodes']:,} nodes, "
              f"{stats['total_edges']:,} edges")
    except Exception as exc:
        print(f"❌  Fatal (graph): {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Load NLP model for semantic search
    #    Prefer offline (HF_HUB_OFFLINE=1) — works when model is pre-cached.
    #    If unavailable and HF_ENDPOINT is set (e.g. hf-mirror.com for China),
    #    fall back to online mode via the mirror.
    try:
        print(f"🧠 Loading NLP model: {VECTOR_MODEL_NAME} (offline mode) ...")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        vector_model = SentenceTransformer(VECTOR_MODEL_NAME, local_files_only=True)
        print(f"   ✓  Model loaded (dim={vector_model.get_sentence_embedding_dimension()})")
    except Exception as exc:
        # If offline fails, try with HF mirror (unset HF_HUB_OFFLINE)
        if os.environ.get("HF_ENDPOINT"):
            try:
                print(f"   ↳ offline failed, trying via {os.environ['HF_ENDPOINT']} ...")
                os.environ.pop("HF_HUB_OFFLINE", None)
                vector_model = SentenceTransformer(VECTOR_MODEL_NAME, local_files_only=False)
                print(f"   ✓  Model loaded (dim={vector_model.get_sentence_embedding_dimension()})")
            except Exception as exc2:
                print(f"   ⚠  Model unavailable: {exc2}  — semantic search disabled")
        else:
            print(f"   ⚠  Model unavailable: {exc}  — semantic search disabled")
            print(f"   💡 Fix: HF_ENDPOINT=https://hf-mirror.com python3 -c \\\"")
            print(f"      from sentence_transformers import SentenceTransformer")
            print(f"      SentenceTransformer('all-MiniLM-L6-v2')\\\"")

    # 3. Load FAISS index
    try:
        if os.path.isfile(FAISS_INDEX_PATH) and os.path.isfile(FAISS_MAPPING_PATH):
            vector_index = faiss.read_index(FAISS_INDEX_PATH)
            with open(FAISS_MAPPING_PATH, "rb") as f:
                vector_mapping = pickle.load(f)
            print(f"   ✓  FAISS index loaded ({vector_index.ntotal:,} vectors)")
        else:
            print(f"   ⚠  FAISS index not found — run build_vector_index.py first")
    except Exception as exc:
        print(f"   ⚠  FAISS index load failed: {exc}")

    print("=" * 60 + "\n")

    # 4. Load layperson dictionary (optional — graceful fallback)
    try:
        if os.path.isfile(LAYERSON_DICT_PATH):
            with open(LAYERSON_DICT_PATH, encoding="utf-8") as f:
                layperson_dict.update(json.load(f))
            print(f"   ✓  Layperson dictionary loaded ({len(layperson_dict):,} terms)")
        else:
            print("   ⚠  layperson_dict.json not found — public mode uses original names")
            print("      Run build_layperson_dict.py to generate translations")
    except Exception as exc:
        print(f"   ⚠  Layperson dict load failed: {exc}  — public mode uses original names")

    # 5. Load SciSpacy biomedical NLP model for intelligent segmentation
    try:
        if spacy is None:
            raise ImportError("spacy not installed")
        print("🧪 Loading SciSpacy model: en_core_sci_sm ...")
        nlp = spacy.load("en_core_sci_sm", disable=["lemmatizer"])
        print(f"   ✓  SciSpacy model loaded (pipeline: {nlp.pipe_names})")
    except Exception as exc:
        nlp = None
        print(f"   ⚠  SciSpacy unavailable: {exc}  — falling back to regex split")

    print("=" * 60 + "\n")
    yield  # application runs here
    # Shutdown: nothing to clean up


# ---------------------------------------------------------------------------
# FastAPI application (must come after lifespan definition)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Explainable Rare Disease Clue Finder",
    description="Knowledge Graph API — True Path Rule inference over HPO + Orphanet",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS (allow frontend dev server) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def serve_frontend():
    """Serve the main frontend HTML page."""
    index_path = FRONTEND_INDEX_PATH
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_path, media_type="text/html",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Pragma": "no-cache",
                            "Expires": "0",
                        })


@app.get("/api/health", response_model=HealthResponse)
def health_check():
    """Return server health and knowledge-graph statistics."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    return HealthResponse(status="ok", graph_stats=graph.stats())


@app.post("/api/predict", response_model=PredictResponse)
def predict_diseases(req: PredictRequest, mode: str = "expert"):
    """Run the True Path Rule inference and return the top-5 diseases.

    Each disease prediction includes explainable matched paths so the UI
    can show *why* a disease was suggested.

    Also returns `suggested_hpos` — the next best symptoms to ask about,
    selected for their discriminative power across the top-5 diseases.

    Query parameters:
        mode — "expert" (default) for original HPO names,
               "public" for layperson-translated names
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    try:
        results = graph.predict(req.hpo_ids)
        # Extract top disease IDs for suggestion engine
        top_disease_ids = [r.disease_id for r in results]
        input_set = set(req.hpo_ids)
        suggestions = graph.suggest_next_hpos(top_disease_ids, input_set)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # --- Translate names for public mode ---
    if mode == "public":
        for s in suggestions:
            s.name = resolve_name(s.hpo_id, s.name, "public")
            s.reason = s.reason  # keep reason as-is (refers to disease name)
        for disease in results:
            for path in disease.matched_paths:
                path.input_hpo_name = resolve_name(path.input_hpo_id, path.input_hpo_name, "public")
                path.matched_hpo_name = resolve_name(path.matched_hpo_id, path.matched_hpo_name, "public")
                path.explanation = path.explanation  # keep explanation structure

    return PredictResponse(
        query_hpo_ids=req.hpo_ids,
        results=results,
        suggested_hpos=suggestions,
    )


@app.get("/api/hpo-search", response_model=HPOSearchResponse)
def search_hpo(q: str = "", limit: int = 20, mode: str = "expert"):
    """Search HPO phenotype terms by name (case-insensitive substring).

    Query parameters:
        q     — search string (min 2 characters)
        limit — max results (default 20, max 50)
        mode  — "expert" (default) for original HPO names,
                "public" for layperson-translated names
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    q = q.strip()
    if len(q) < 2:
        return HPOSearchResponse(query=q, results=[], total=0)
    limit = max(1, min(limit, 50))
    results = graph.search_hpo(q, limit=limit)
    return HPOSearchResponse(
        query=q,
        results=[HPOSearchResult(
            id=r[0],
            name=resolve_name(r[0], r[1], mode)
        ) for r in results],
        total=len(results),
    )


@app.get("/api/anatomy-tree")
def anatomy_tree():
    """Return the top-level HPO categories — direct children of
    HP:0000118 (Phenotypic abnormality).  These are the major
    anatomical/physiological system nodes for an anatomy-tree UI.
    """
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    return graph.get_anatomy_tree()


@app.get("/api/vector-search", response_model=VectorSearchResponse)
def vector_search(text: str = "", k: int = 5, mode: str = "expert"):
    """Semantic search over HPO terms using sentence-transformers embeddings.

    Encodes free-text layperson descriptions (e.g. "muscle stiffness in legs")
    and returns the top-k closest HPO terms by L2 distance in the FAISS index.

    Query parameters:
        text — free-text symptom description (min 3 characters)
        k    — number of results (default 5, max 10)
        mode — "expert" (default) for original HPO names,
               "public" for layperson-translated names
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if vector_model is None or vector_index is None:
        raise HTTPException(
            status_code=503,
            detail="Vector search not available — model or index not loaded",
        )
    text = text.strip()
    if len(text) < 3:
        return VectorSearchResponse(query=text, results=[])

    k = max(1, min(k, 10))

    # Encode query with sentence-transformers (L2-normalized → cosine via IP)
    query_vec = vector_model.encode(
        [text], normalize_embeddings=True, show_progress_bar=False
    ).astype(np.float32)

    # Search FAISS
    distances, indices = vector_index.search(query_vec, k)

    # Build response
    results: List[VectorSearchResult] = []
    for i in range(k):
        idx = int(indices[0][i])
        if idx < 0 or idx >= len(vector_mapping):
            continue
        entry = vector_mapping[idx]
        results.append(VectorSearchResult(
            hpo_id=entry["hpo_id"],
            name=resolve_name(entry["hpo_id"], entry["name"], mode),
            score=round(float(distances[0][i]), 4),
        ))

    return VectorSearchResponse(query=text, results=results)


@app.get("/api/smart-search", response_model=SmartSearchResponse)
def smart_search(text: str = Query(..., min_length=3, description="Free-text symptom description"),
                 mode: str = Query("expert", description="Mode: 'expert' or 'public'")):
    """Phase 8: SciSpacy-powered intelligent segmentation + hybrid search.

    Accepts free-text layperson descriptions (e.g. "heavy feeling in legs, trouble walking"),
    uses SciSpacy biomedical NLP to extract clean symptom fragments, then performs
    hybrid search (vector + keyword) per fragment with cross-fragment deduplication.

    Returns grouped results keyed by extracted fragment for the frontend UI.
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    if vector_model is None or vector_index is None:
        missing = []
        if vector_model is None: missing.append("NLP model (all-MiniLM-L6-v2)")
        if vector_index is None: missing.append("FAISS index (hpo_index.faiss)")
        raise HTTPException(
            status_code=503,
            detail=f"Smart search unavailable — missing: {', '.join(missing)}. "
                   f"Run: pip install sentence-transformers faiss-cpu && "
                   f"python3 -c \\\"from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')\\\" && "
                   f"python3 build_vector_index.py",
        )

    text = text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Query must be at least 3 characters")

    # 1. SciSpacy segmentation → clean fragments
    fragments = extract_symptom_fragments(text)

    # 2. For each fragment, run hybrid search (vector + keyword in parallel)
    groups: List[SmartSearchGroup] = []
    global_best: Dict[str, Tuple[float, str, float, str]] = {}  # hpo_id → (best_score, name, _kw_flag, hint)

    for fragment in fragments:
        # 2a. Vector search
        vec_results: List[VectorSearchResult] = []
        try:
            query_vec = vector_model.encode(
                [fragment], normalize_embeddings=True, show_progress_bar=False
            ).astype(np.float32)
            distances, indices = vector_index.search(query_vec, 6)
            for i in range(min(6, len(indices[0]))):
                idx = int(indices[0][i])
                if idx < 0 or idx >= len(vector_mapping):
                    continue
                entry = vector_mapping[idx]
                score = round(float(distances[0][i]), 4)
                vec_results.append(VectorSearchResult(
                    hpo_id=entry["hpo_id"],
                    name=resolve_name(entry["hpo_id"], entry["name"], mode),
                    score=score,
                    hint=resolve_parent_hint(entry["hpo_id"]) if mode == "public" else "",
                ))
        except Exception:
            pass  # vector search failed for this fragment — skip

        # 2b. Keyword search
        kw_results: List[VectorSearchResult] = []
        is_bio = is_biomarker_fragment(fragment)
        kw_base_score = 1.5 if is_bio else 1.0  # Phase 10: boost biomarker keywords
        try:
            hpo_matches = graph.search_hpo(fragment, limit=6)
            for hpo_id, name in hpo_matches:
                hint = ""
                if mode == "public" and is_bio:
                    hint = "🧪 Lab"
                elif mode == "public":
                    hint = resolve_parent_hint(hpo_id)
                kw_results.append(VectorSearchResult(
                    hpo_id=hpo_id,
                    name=resolve_name(hpo_id, name, mode),
                    score=kw_base_score,
                    hint=hint,
                ))
        except Exception:
            pass

        # 2c. Merge: keyword first, then vector (dedup within fragment)
        seen_frag: Set[str] = set()
        merged: List[VectorSearchResult] = []
        for r in kw_results:
            if r.hpo_id not in seen_frag:
                seen_frag.add(r.hpo_id)
                merged.append(r)
        for r in vec_results:
            if r.hpo_id not in seen_frag:
                seen_frag.add(r.hpo_id)
                merged.append(r)

        # 2d. Track global best across fragments for dedup (include hint)
        for r in merged:
            current = global_best.get(r.hpo_id)
            if current is None or r.score > current[0]:
                global_best[r.hpo_id] = (r.score, r.name, 1.0 if r.score >= kw_base_score else 0.0, r.hint)

        groups.append(SmartSearchGroup(
            fragment=fragment,
            results=merged[:5],
        ))

    # 3. Deduplicate across fragments: keep only the best-scoring occurrence
    deduped_groups: List[SmartSearchGroup] = []
    for group in groups:
        deduped: List[VectorSearchResult] = []
        for r in group.results:
            best = global_best.get(r.hpo_id, (r.score, r.name, 0.0, r.hint))
            best_score, best_name, is_kw, best_hint = best
            if r.score >= best_score * 0.99:  # within 1% of best → keep in this group
                deduped.append(VectorSearchResult(
                    hpo_id=r.hpo_id,
                    name=best_name,
                    score=r.score,
                    hint=best_hint,
                ))
        if deduped:
            deduped_groups.append(SmartSearchGroup(
                fragment=group.fragment,
                results=deduped,
            ))

    return SmartSearchResponse(
        query=text,
        fragments=fragments,
        groups=deduped_groups,
    )


# ---------------------------------------------------------------------------
# Main (for direct execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # NOTE: Must use app instance (not "main:app" string) when running as __main__.
    # The string form causes uvicorn to create a second "main" module import,
    # which means lifespan-loaded globals (vector_model, vector_index) end up
    # in a different module than the one handling requests → 503 on all endpoints.
    # For reload support, run: python3 -m uvicorn main:app --reload
    uvicorn.run(app, host="0.0.0.0", port=8000)
