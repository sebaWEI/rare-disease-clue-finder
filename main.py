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
GET  /api/guides/{id}     — care-guide experts for a disease (WHS first)
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
from urllib.parse import unquote

import faiss
import networkx as nx
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_dotenv(path: str) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no overwrite)."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"⚠  Could not read .env: {exc}")


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_load_dotenv(os.path.join(_BASE_DIR, ".env"))

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(_BASE_DIR, ""),
)
# React (Vite) production build lives in frontend/dist.
FRONTEND_DIST_DIR = os.path.join(_BASE_DIR, "frontend", "dist")
FRONTEND_INDEX_PATH = os.path.join(FRONTEND_DIST_DIR, "index.html")
NODES_CSV = os.path.join(DATA_DIR, "nodes.csv")
EDGES_HPO_CSV = os.path.join(DATA_DIR, "edges_hpo.csv")
EDGES_DISEASE_HPO_CSV = os.path.join(DATA_DIR, "edges_disease_hpo.csv")
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "hpo_index.faiss")
FAISS_MAPPING_PATH = os.path.join(DATA_DIR, "hpo_mapping.pkl")
LAYERSON_DICT_PATH = os.path.join(DATA_DIR, "layperson_dict.json")
LAYERSON_DICT_ZH_PATH = os.path.join(DATA_DIR, "layperson_dict_zh.json")
HPO_NAMES_ZH_PATH = os.path.join(DATA_DIR, "hpo_names_zh.json")
DISEASE_DICT_ZH_PATH = os.path.join(DATA_DIR, "disease_names_zh.json")
GUIDES_DIR = os.path.join(_BASE_DIR, "data", "guides")
HOSPITALS_JSON_PATH = os.path.join(_BASE_DIR, "data", "hospitals.json")
HOSPITAL_LOGO_MAP_PATH = os.path.join(_BASE_DIR, "data", "hospital_logo_map.json")
HOSPITAL_LOGO_DIR = os.path.join(_BASE_DIR, "data", "hospital-logos")

# --- API clients (cloud-based, replaces local models) ---

# 1. DeepSeek client — for symptom NER extraction (Phase 8)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-your-deepseek-key")
deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)

# 2. Embedding client — for FAISS vector search (Phase 6)
#    SiliconFlow provides free BAAI/bge-m3 (1024-dim), OpenAI-compatible
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "sk-your-embedding-key")
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024  # bge-m3 dimension

embedding_client = OpenAI(
    api_key=EMBEDDING_API_KEY,
    base_url=EMBEDDING_BASE_URL,
)

# Inference multipliers (True Path Rule)
SCORE_DIRECT    = 1.0   # disease has the exact HPO term
BASE_INDIRECT   = 0.5   # base weight for child/parent matches
                         # actual = BASE_INDIRECT × exp(-n) where n = number of input HPOs
                         # This exponentially suppresses indirect matches when many
                         # symptoms are provided, preventing broad-term domination.
PARENT_MAX_HOPS = 2     # limit ancestor traversal depth

TOP_N = 5              # number of diseases to return
TOP_CORE_TIER1 = 15    # top K from Obligate+Very Frequent (freq >= 0.9) for explained ratio
TOP_CORE_TIER2 = 15    # top K from Frequent (0.55 <= freq < 0.9) for explained ratio
                        # Two-tier ensures both textbook signs AND clinically reportable
                        # symptoms contribute to the denominator.
COVERAGE_EXPONENT = 0.3  # power-law smoothing: f(x)=x^α maps 10%→50%, preserves 0→0, 1→1

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


class MissingCriticalHPO(BaseModel):
    """A missing obligate symptom flagged for triage / secondary confirmation."""
    hpo_id: str
    name: str  # mode+lang resolved display name


class DiseasePrediction(BaseModel):
    disease_id: str
    disease_name: str
    total_score: float                     # match strength (IC-weighted)
    matched_paths: List[MatchedPath]
    explained_ratio: float = 0.0           # patient core IC / disease core IC
    missing_critical_hpos: List[MissingCriticalHPO] = []  # top 2-3 missing obligate HPOs


class SuggestedHPO(BaseModel):
    hpo_id: str
    name: str
    reason: str


class AutoSelection(BaseModel):
    """Trace of how a free-text symptom was auto-mapped to an HPO term."""
    raw_description: str
    standard_term: str
    matched_hpo_id: str
    matched_hpo_name: str
    match_method: str  # "llm_rerank" | "keyword_fallback" | "none"


class SymptomLog(BaseModel):
    """Per-symptom computation trace for the explainability log."""
    raw_description: str
    standard_term: str
    keyword_candidates: List[Dict[str, str]] = []   # [{hpo_id, name, match_field}]
    faiss_candidates: List[Dict[str, Any]] = []     # [{hpo_id, name, score}]
    reranker_selection: Optional[str] = None         # selected hpo_id or None (rejected)
    reranker_rejected: bool = False                  # True if DeepSeek returned null
    selected_hpo_id: str = ""
    selected_hpo_name: str = ""
    match_method: str = ""


class ContributionLog(BaseModel):
    """Single contribution trace in the inference engine."""
    hpo_id: str
    hpo_name: str
    match_type: str          # direct | child | parent
    frequency_weight: float
    multiplier: float
    idf_weight: float
    contribution: float


class DiseaseScoreLog(BaseModel):
    """Per-disease score breakdown."""
    disease_id: str
    disease_name: str
    total_score: float
    explained_ratio: float = 0.0
    contributions: List[ContributionLog] = []


class InferenceLog(BaseModel):
    """True Path Rule inference computation trace."""
    total_diseases: int = 0
    input_hpo_ids: List[str] = []
    disease_scores: List[DiseaseScoreLog] = []


class ComputationLog(BaseModel):
    """Full computation trace for explainability."""
    extraction: Dict[str, Any] = {}
    symptoms: List[SymptomLog] = []
    inference: InferenceLog = InferenceLog()


class PredictResponse(BaseModel):
    query_hpo_ids: List[str]
    results: List[DiseasePrediction]
    suggested_hpos: List[SuggestedHPO] = []
    auto_selections: List[AutoSelection] = []       # populated by auto-diagnose mode
    computation_log: Optional[ComputationLog] = None # detailed trace when verbose


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


class GuideHospital(BaseModel):
    id: str
    name: str
    city: str
    map_query: str = ""  # Chinese name for map deep-links (Amap/Baidu)
    lat: Optional[float] = None
    lng: Optional[float] = None
    logo_url: Optional[str] = None
    advantage: str = ""


class GuideExpert(BaseModel):
    id: str
    name: str
    type: str  # doctor | team | department
    bio: str = ""
    hospital: GuideHospital


class DiseaseGuideResponse(BaseModel):
    disease_id: str
    available: bool
    name: str = ""
    name_alt: str = ""
    summary: str = ""
    care_tips: List[str] = []
    specialty_keywords: List[str] = []
    experts: List[GuideExpert] = []
    cities: List[str] = []
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 15: Missing-symptom layperson descriptions (DeepSeek)
# ---------------------------------------------------------------------------

class DiseasePredictionSummary(BaseModel):
    """Lightweight disease prediction for the describe-missing endpoint."""
    disease_id: str
    disease_name: str
    matched_hpo_names: List[str] = []        # HPO names the patient DOES have
    missing_hpo_names: List[str] = []         # HPO names the patient is MISSING
    explained_ratio: float = 0.0


class DescribeMissingRequest(BaseModel):
    original_text: str                        # patient's original symptom narrative
    predictions: List[DiseasePredictionSummary]  # top-5 disease summaries
    lang: str = "en"                          # "en" or "zh"


class DiseaseDescription(BaseModel):
    disease_id: str
    disease_name: str
    description: str                          # DeepSeek-generated plain-language description


class DescribeMissingResponse(BaseModel):
    descriptions: List[DiseaseDescription]


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
        self._hpo_idf: Dict[str, float] = {}                    # pre-computed sqrt-smoothed IDF per HPO
        self._disease_core_ic_sum: Dict[str, float] = {}        # Σ(freq × idf) for top-K core HPOs
        self._disease_top_core_hpos: Dict[str, List[Tuple[str, float, float]]] = {}  # disease_id → [(hpo_id, freq, idf)]
        # True Path Rule: pre-computed annotation closure (disease → all ancestors of its HPOs)
        self._expanded_disease_hpos: Dict[str, Dict[str, Tuple[str, float]]] = {}
        # disease_id → {ancestor_hpo: (original_source_hpo, freq_weight)}
        # Inverted index for O(1) lookup: hpo_id → [(disease_id, source_hpo, freq)]
        self._hpo_to_expanded_diseases: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)

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
        self._build_expanded_disease_hpos()

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
                definition = row.get("definition", "")
                self.G.add_node(
                    node_id,
                    name=name,
                    type=node_type,
                    synonyms=synonyms,
                    definition=definition,
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
        """Pre-compute static lookup tables: HPO IDF scores + disease core IC sums.

        IDF (sqrt-smoothed Information Content):
            idf = 1.0 + sqrt(log(total_diseases / (df + 1)))
        Same formula as used in predict() — ensures explained_ratio is
        consistent with match strength scoring.

        Core threshold: freq_weight >= 0.55 (Obligate + Very frequent + Frequent).
        Denominator = Σ(freq × idf) for each disease's core HPOs — computed
        once at startup, used as O(1) division in predict().
        """
        total_diseases = max(1, len(self._disease_id_to_name))

        # --- Step 1: IDF per HPO ---
        for hpo_id, disease_list in self._hpo_to_diseases.items():
            df = len(disease_list)
            if df == 0:
                self._hpo_idf[hpo_id] = 1.0
            else:
                raw_idf = math.log(total_diseases / (df + 1))
                self._hpo_idf[hpo_id] = 1.0 + math.sqrt(raw_idf)

        # --- Step 2: Two-tier core IC sum per disease ---
        # Tier 1: Obligate + Very Frequent (freq >= 0.9) — textbook diagnostic markers
        # Tier 2: Frequent (0.55 <= freq < 0.9) — clinically reportable symptoms
        #
        # Taking top K from EACH tier ensures the denominator includes BOTH
        # highly discriminative but rarely-reported markers (enzyme levels, biopsy)
        # AND symptoms patients actually complain about (pain, proteinuria).
        # A single global Top-K gets dominated by tier-1 high-IDF terms.
        for disease_id, hpo_list in self._disease_to_hpos.items():
            tier1: List[Tuple[str, float, float]] = []  # freq >= 0.9
            tier2: List[Tuple[str, float, float]] = []  # 0.55 <= freq < 0.9
            for hpo_id, freq_weight in hpo_list:
                if freq_weight >= 0.9:
                    idf_val = self._hpo_idf.get(hpo_id, 1.0)
                    tier1.append((hpo_id, freq_weight, idf_val))
                elif freq_weight >= 0.55:
                    idf_val = self._hpo_idf.get(hpo_id, 1.0)
                    tier2.append((hpo_id, freq_weight, idf_val))
            # Sort each tier by IDF descending
            tier1.sort(key=lambda x: x[2], reverse=True)
            tier2.sort(key=lambda x: x[2], reverse=True)
            # Take top K from each tier
            top_k = tier1[:TOP_CORE_TIER1] + tier2[:TOP_CORE_TIER2]
            self._disease_top_core_hpos[disease_id] = top_k
            # Denominator = Σ(freq × idf) for these top-K HPOs
            core_sum = sum(freq * idf for _, freq, idf in top_k)
            self._disease_core_ic_sum[disease_id] = core_sum

        print(f"   ✓  {len(self._hpo_idf):,} HPO IDFs computed", flush=True)
        print(f"   ✓  {len(self._disease_core_ic_sum):,} disease core IC sums"
              f" (tier1-{TOP_CORE_TIER1}+tier2-{TOP_CORE_TIER2}) computed", flush=True)

    def _build_expanded_disease_hpos(self) -> None:
        """Pre-compute True Path Rule annotation closure.

        For each disease, expand its directly annotated HPOs upward through
        the 'is_a' hierarchy to include all ancestors (more general terms).
        This is the standard ontology inference: if a disease has "Generalized
        seizure", it also has "Seizure" and "Abnormal nervous system physiology".

        Stores:
          _expanded_disease_hpos[disease][ancestor_hpo] = (source_hpo, freq_weight)
          _hpo_to_expanded_diseases[hpo] = [(disease_id, source_hpo, freq_weight), ...]
        """
        print("🧬 Building True Path Rule annotation closure ...", flush=True)
        total_ancestors = 0
        for disease_id, hpo_list in self._disease_to_hpos.items():
            expanded: Dict[str, Tuple[str, float]] = {}
            for hpo_id, freq_weight in hpo_list:
                # The HPO itself is part of the closure
                if hpo_id not in expanded or freq_weight > expanded[hpo_id][1]:
                    expanded[hpo_id] = (hpo_id, freq_weight)
                # All ancestors (more general) up the hierarchy
                try:
                    for ancestor in nx.descendants(self.G, hpo_id):
                        if ancestor in self._hpo_id_to_name:
                            if ancestor not in expanded or freq_weight > expanded[ancestor][1]:
                                expanded[ancestor] = (hpo_id, freq_weight)
                except nx.NetworkXError:
                    pass
            self._expanded_disease_hpos[disease_id] = expanded
            total_ancestors += len(expanded)
            # Build inverted index
            for ancestor, (source_hpo, freq_weight) in expanded.items():
                self._hpo_to_expanded_diseases[ancestor].append(
                    (disease_id, source_hpo, freq_weight)
                )

        avg_closure = total_ancestors / max(1, len(self._disease_to_hpos))
        print(f"   ✓  {total_ancestors:,} total expanded HPOs "
              f"({avg_closure:.1f} avg per disease)", flush=True)
        total_indexed = len(self._hpo_to_expanded_diseases)
        print(f"   ✓  {total_indexed:,} HPOs in inverted disease index", flush=True)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _compute_disease_concern(
        self,
        disease_id: str,
        tpr_matched_source_hpos: Set[str],
    ) -> Tuple[float, List[MissingCriticalHPO]]:
        """Compute explained ratio + missing critical symptoms.

        Parameters
        ----------
        disease_id : str
            The disease to evaluate.
        tpr_matched_source_hpos : set of str
            The disease's directly-annotated HPO IDs that were matched
            via True Path Rule (patient input overlaps with the disease's
            annotation closure). TPR already guarantees hierarchy coverage:
            if patient inputs "Seizure" and disease has "Generalized seizure",
            "Generalized seizure" appears here — no separate tree traversal needed.

        Returns
        -------
        (explained_ratio, missing_critical_hpos)
        """
        core_total = self._disease_core_ic_sum.get(disease_id, 0.0)
        if core_total <= 0:
            return 0.0, []

        # --- Gold-core set: Top-K HPO IDs that define the denominator ---
        # Numerator only counts matches inside this set — strict isolation from
        # predict()'s full-match loop. The denominator is also built from this
        # exact set (pre-computed in _build_lookups), so ratio is self-consistent.
        gold_core_set = {hpo_id for hpo_id, _, _ in self._disease_top_core_hpos.get(disease_id, [])}

        core_matched_ic = 0.0
        obligate_missing: List[Tuple[str, str, float]] = []

        for hpo_id, freq_weight in self._disease_to_hpos.get(disease_id, []):
            is_matched = hpo_id in tpr_matched_source_hpos
            idf_val = self._hpo_idf.get(hpo_id, 1.0)

            # Numerator: only gold-core HPOs that the patient actually matched
            if hpo_id in gold_core_set and is_matched:
                core_matched_ic += freq_weight * idf_val

            # Missing critical: freq >= 0.9 AND not matched (independent check)
            if freq_weight >= 0.9 and not is_matched:
                hpo_name = self._hpo_id_to_name.get(hpo_id, hpo_id)
                obligate_missing.append((hpo_id, hpo_name, idf_val))

        raw_ratio = core_matched_ic / core_total
        # Power-law smoothing: f(x) = x^α  maps 10%→50%, 0→0, 1→1
        # Prevents multi-system diseases from showing single-digit coverage
        # when 3-5 common but non-top symptoms all match.
        explained_ratio = raw_ratio ** COVERAGE_EXPONENT

        # --- Missing obligates: sort by IDF desc, take top 3 ---
        obligate_missing.sort(key=lambda x: x[2], reverse=True)
        top_missing = [
            MissingCriticalHPO(hpo_id=hid, name=name)
            for hid, name, _ in obligate_missing[:3]
        ]

        return explained_ratio, top_missing

    # ------------------------------------------------------------------
    # Inference

    def predict(self, hpo_ids: List[str]) -> List[DiseasePrediction]:
        """Run True Path Rule inference via pre-computed annotation closure.

        True Path Rule (standard ontology inference):
          If a disease has HPO:Child, it also has HPO:Parent, HPO:Grandparent, …
          This is pre-computed at startup as _expanded_disease_hpos.

        At query time, matching is O(1) dictionary lookup — no graph traversal.
        One user symptom → one best evidence path per disease (no duplicate
        scoring from fine-grained annotations).

        Score(disease) = Σ [ frequency_weight × 1.0 × IDF ]
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

        disease_scores: Dict[str, float] = defaultdict(float)
        disease_paths: Dict[str, List[MatchedPath]] = defaultdict(list)
        # For concern: per disease, which directly-annotated HPOs were matched via TPR?
        # TPR closure guarantees: if patient inputs an ancestor, the disease's more
        # specific child HPO IS covered. No need for separate hierarchy traversal.
        tpr_matched_source_hpos: Dict[str, Set[str]] = defaultdict(set)
        total_diseases = max(1, len(self._disease_id_to_name))

        for input_hpo in valid_hpos:
            input_name = self._hpo_id_to_name.get(input_hpo, input_hpo)

            # Per (disease_id, input_hpo), keep only the best source HPO
            # (highest freq_weight) from the annotation closure.
            # This prevents ontology annotation bias: a disease with 3 child
            # terms all expanding to the same ancestor gets scored once, not 3×.
            best_per_disease: Dict[str, Tuple[str, float]] = {}  # disease_id → (source_hpo, freq_weight)
            for disease_id, source_hpo, freq_weight in self._hpo_to_expanded_diseases.get(input_hpo, []):
                if disease_id not in best_per_disease or freq_weight > best_per_disease[disease_id][1]:
                    best_per_disease[disease_id] = (source_hpo, freq_weight)

            for disease_id, (source_hpo, freq_weight) in best_per_disease.items():
                # Track BOTH the source HPO AND the input HPO — they may differ
                # when TPR matches via ancestor propagation.
                tpr_matched_source_hpos[disease_id].add(source_hpo)
                tpr_matched_source_hpos[disease_id].add(input_hpo)
                source_name = self._hpo_id_to_name.get(source_hpo, source_hpo)
                match_type = "direct" if source_hpo == input_hpo else "tpr"

                mult = SCORE_DIRECT
                df = len(self._hpo_to_diseases.get(source_hpo, []))
                raw_idf = math.log(total_diseases / (df + 1))
                idf = 1.0 + math.sqrt(raw_idf)
                contribution = freq_weight * mult * idf
                disease_scores[disease_id] += contribution
                disease_paths[disease_id].append(
                    MatchedPath(
                        input_hpo_id=input_hpo,
                        input_hpo_name=input_name,
                        matched_hpo_id=source_hpo,
                        matched_hpo_name=source_name,
                        match_type=match_type,
                        frequency_weight=freq_weight,
                        score_multiplier=mult,
                        contribution=round(contribution, 4),
                        explanation=self._build_explanation(
                            input_name, source_name, match_type, freq_weight
                        ) + f" [IDF: {round(idf, 2)}]",
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
            matched_source_set = tpr_matched_source_hpos.get(disease_id, set())
            explained_ratio, missing_critical = self._compute_disease_concern(
                disease_id, matched_source_set
            )
            results.append(
                DiseasePrediction(
                    disease_id=disease_id,
                    disease_name=disease_name,
                    total_score=round(score, 4),
                    matched_paths=paths,
                    explained_ratio=round(explained_ratio, 4),
                    missing_critical_hpos=missing_critical,
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
            if idx == -1:
                # Also search definition (bridges layperson terms to HPO)
                definition = node_data.get("definition", "")
                if definition:
                    idx = definition.lower().find(query_lower)
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
vector_index: Optional[Any] = None   # faiss.Index
vector_mapping: List[Dict[str, str]] = []  # [{hpo_id, name}, ...]
layperson_dict: Dict[str, str] = {}  # HP:XXXXXXX → layperson phrase
layperson_dict_zh: Dict[str, str] = {}  # HP:XXXXXXX → 中文通俗说法
hpo_names_zh: Dict[str, str] = {}       # HP:XXXXXXX → 中文专业术语
disease_dict_zh: Dict[str, str] = {}   # ORPHA:ID → 中文疾病名
# Care-guide data (WHS first)
guide_hospitals: Dict[str, Dict[str, Any]] = {}  # hospital_id → hospital
guide_by_disease: Dict[str, Dict[str, Any]] = {}  # disease_id → guide doc


def resolve_name(hpo_id: str, fallback_name: str, mode: str = "expert",
                 lang: str = "en") -> str:
    """Return the display name for an HPO term based on mode and language.

    - expert mode: returns the original HPO name (or Chinese medical term)
    - public mode: returns the layperson translation if available,
      otherwise falls back to the original name

    lang="zh": uses Chinese dictionaries (layperson_dict_zh / hpo_names_zh).
           Falls back to English if the zh dict is missing or key absent.
    """
    if lang == "zh":
        if mode == "public" and hpo_id in layperson_dict_zh:
            return layperson_dict_zh[hpo_id]
        if hpo_id in hpo_names_zh:
            # expert mode in zh: use Chinese medical term
            # public mode without zh layperson: also use Chinese medical term as fallback
            return hpo_names_zh[hpo_id]
        # zh dicts not available → fall through to English
    if mode == "public" and hpo_id in layperson_dict:
        return layperson_dict[hpo_id]
    return fallback_name


def resolve_disease_name(disease_id: str, fallback_name: str, lang: str = "en") -> str:
    """Return the display name for a disease based on language."""
    if lang == "zh" and disease_id in disease_dict_zh:
        return disease_dict_zh[disease_id]
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


def extract_symptom_fragments(text: str, lang: str = "en") -> List[Dict[str, str]]:
    """Use DeepSeek API for zero-shot medical entity extraction + HPO-aligned standardization.

    Returns a list of dicts with two keys:
      - "raw":   the user's original layperson description (for UI display)
      - "standard": a medical term as close as possible to HPO ontology vocabulary
                     (for FAISS vector search — NO raw_description mixed in)

    The separation is critical: mixing raw + standard in one embedding string
    causes "semantic dilution" — the model's attention is scattered across
    high-frequency layperson words (hands, feet, pain), drowning out the
    precise medical signal needed for HPO ontology matching.

    When lang="zh", the input is Chinese and the prompt instructs DeepSeek
    to map Chinese symptoms to English HPO terms for downstream search.

    Falls back to regex split on API failure.
    """
    text = text.strip()
    if not text:
        return []

    if lang == "zh":
        prompt = f"""你是一位罕见病症状提取专家。
分析以下中文症状描述，拆分为独立的、原子化的医学症状。
规则：
1. 将复杂描述拆分为单个症状（例如 "手脚疼痛，身上有红色斑点" → 两个独立症状）。
2. raw_description 保留用户的中文原话，standard_term 必须输出精确的英文 HPO 术语。
3. standard_term 优先使用最精确的 HPO 术语名，而非泛化描述。示例：
   - "皮肤上的深红色斑点" → "Angiokeratoma"（不是 "red spots" 或 "purpura"）
   - "手脚灼烧感" → "Acroparesthesia"（不是 "burning pain"）
   - "不出汗" → "Anhidrosis"（不是 "cannot sweat"）
   - "发烧" → "Fever"
   - "肚子疼" → "Abdominal pain"
   - "反复抽搐" → "Seizure"
   - "肌肉无力" → "Muscle weakness"
   - "视力模糊" → "Blurred vision"
4. 只返回 JSON 数组，每个对象含 "raw_description" 和 "standard_term" 两个键。不要用 markdown 包裹。

文本: "{text}"
"""
        system_prompt = (
            "你是一位医学症状提取专家，精通 HPO 本体对齐。"
            "输出包含 'raw_description'（用户中文原话）和 "
            "'standard_term'（英文 HPO 术语）的有效 JSON 数组。"
        )
    else:
        prompt = f"""
You are an expert clinical information extractor.
Analyze the following user text and extract the distinct medical signs and symptoms.
Rules:
1. Break down complex descriptions into individual, atomic symptoms.
2. MUST explicitly include the anatomical location (e.g., instead of "dark spots", output "dark spots on abdominal skin").
3. For 'standard_term', output the EXACT HPO (Human Phenotype Ontology) term name whenever possible — not a general clinical description. Examples: "dark red spots on skin" → "Angiokeratoma" (not "purpura" or "red spots"); "burning pain in hands and feet" → "Acroparesthesia" (not "burning paresthesia"); "cannot sweat" → "Anhidrosis" (not "absence of sweating"). Use the most specific HPO term available. Prefer Latin/Greek medical nomenclature over descriptive phrases.
4. Return ONLY a valid JSON array of objects, each with keys "raw_description" and "standard_term". No markdown blocks.

Text: "{text}"
"""
        system_prompt = (
            "You are a medical JSON extractor specializing in HPO ontology alignment. "
            "Output a valid JSON array of objects with 'raw_description' (user's words) "
            "and 'standard_term' (HPO-aligned medical vocabulary) keys only."
        )

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        result_text = response.choices[0].message.content.strip()

        # Clean possible markdown wrapping
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            result_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        fragments = json.loads(result_text)
        if not isinstance(fragments, list):
            return [{"raw": text, "standard": text}]

        # Build structured fragments
        structured: List[Dict[str, str]] = []
        for item in fragments:
            if isinstance(item, dict):
                raw = item.get("raw_description", "").strip().lower()
                std = item.get("standard_term", "").strip().lower()
                if raw and std:
                    structured.append({"raw": raw, "standard": std})
                elif raw:
                    structured.append({"raw": raw, "standard": raw})
                elif std:
                    structured.append({"raw": std, "standard": std})
            elif isinstance(item, str):
                s = item.strip().lower()
                if s:
                    structured.append({"raw": s, "standard": s})

        return structured or [{"raw": text, "standard": text}]

    except Exception as e:
        print(f"DeepSeek API extraction failed: {e} — falling back to regex split")
        import re
        splits = [p.strip() for p in re.split(
            r'[,;.，；。、\n]+|\band\b|\balso\b|和|还有|以及|\n',
            text, flags=re.IGNORECASE
        ) if len(p.strip()) >= 3]
        return [{"raw": s, "standard": s} for s in splits] or [{"raw": text, "standard": text}]


def get_api_embedding(text: str) -> np.ndarray:
    """Call cloud Embedding API and return FAISS-compatible numpy array.

    Uses SiliconFlow's BAAI/bge-m3 (1024-dim). Returns shape (1, 1024) float32.

    Expects a PURE medical standard_term (from DeepSeek extraction), NOT the
    user's raw layperson description. The caller (smart_search) is responsible
    for decoupling: raw_description for UI display, standard_term for embedding.

    Adds an instruction prefix for asymmetric retrieval — signals the model
    to encode this as a search query targeting the HPO ontology index.
    The FAISS index (built by build_vector_index.py) is embedded WITHOUT
    any prefix, creating the query/document asymmetry that BGE models excel at.
    """
    instruction = (
        "Represent this medical symptom for retrieving relevant "
        "rare disease phenotype terms from a standard ontology: "
    )
    query_text = f"{instruction}{text}"

    try:
        response = embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=query_text,
        )
        vec = response.data[0].embedding
        return np.array([vec], dtype=np.float32)
    except Exception as e:
        print(f"Embedding API request failed: {e}")
        raise ValueError("Failed to fetch embedding from API")


def llm_rerank_candidates(
    raw_description: str,
    candidates: List[Dict[str, str]],
    full_text: str = "",
) -> Optional[str]:
    """DeepSeek reranks a unified candidate pool (keyword + FAISS results).

    All candidates — whether from keyword search or FAISS vector recall —
    go into the same pool. DeepSeek reads the patient's original words
    AND the full clinical narrative for context, then selects the single
    best HPO match or rejects all (returns None).

    The full_text parameter gives the model the patient's complete
    description (e.g. "burning pain in hands, dark red spots, cannot sweat"),
    so it can disambiguate: "dark red spots" + "cannot sweat" → Angiokeratoma
    (Fabry disease), not Purpura (generic bleeding).

    Args:
        raw_description: single extracted symptom fragment
        candidates: merged keyword + FAISS candidates
        full_text: the patient's complete original narrative

    Returns:
        selected hpo_id string, or None if no candidate is good enough
    """
    if not candidates:
        return None

    candidates_text = "\n".join(
        f"- ID: {c['hpo_id']} | Term: {c['name']}" for c in candidates
    )

    # Build context-aware prompt
    full_context = (
        f'The patient\'s full description was: "{full_text}"\n\n'
        if full_text else ""
    )
    prompt = f"""You are an expert medical ontology matcher.
The patient reported the following symptom in colloquial language:
"{raw_description}"

{full_context}Below are the candidate medical terms retrieved from the Human Phenotype Ontology (HPO):
{candidates_text}

TASK:
Select the SINGLE candidate that best represents the patient's symptom.
Use the full clinical context to disambiguate — if the patient has multiple
symptoms that together point to a specific disease, prefer the candidate
that fits the overall clinical picture.
If none of the candidates accurately match the symptom, you MUST reject them all.

Output ONLY a valid JSON object with this exact structure:
{{"selected_id": "HPO_ID_HERE"}} OR {{"selected_id": null}} if no match is good enough.
"""

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": "You are a strict clinical JSON reranker. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )

        result_text = response.choices[0].message.content.strip()

        # Clean markdown wrapping
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            result_text = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )

        data = json.loads(result_text)
        return data.get("selected_id")

    except Exception as e:
        print(f"DeepSeek rerank failed: {e}")
        return None


def _apply_logo_map(hospitals: Dict[str, Dict[str, Any]]) -> None:
    """Fill hospital['logo'] from hospital_logo_map.json when present."""
    if not os.path.isfile(HOSPITAL_LOGO_MAP_PATH):
        return
    try:
        with open(HOSPITAL_LOGO_MAP_PATH, encoding="utf-8") as f:
            logos = (json.load(f) or {}).get("logos") or {}
        by_name = {
            (h.get("name_zh") or h.get("name") or ""): h for h in hospitals.values()
        }
        for name, filename in logos.items():
            if name in by_name and filename:
                by_name[name]["logo"] = filename
    except Exception as exc:
        print(f"   ⚠  hospital logo map load failed: {exc}")


def load_care_guides() -> None:
    """Load hospitals + per-disease guide JSON into memory."""
    global guide_hospitals, guide_by_disease
    guide_hospitals = {}
    guide_by_disease = {}

    if os.path.isfile(HOSPITALS_JSON_PATH):
        with open(HOSPITALS_JSON_PATH, encoding="utf-8") as f:
            hosp_doc = json.load(f)
        for h in hosp_doc.get("hospitals") or []:
            guide_hospitals[h["id"]] = dict(h)
        _apply_logo_map(guide_hospitals)
        print(f"   ✓  Care-guide hospitals loaded ({len(guide_hospitals)})")
    else:
        print("   ⚠  data/hospitals.json not found — run scripts/build_whs_guide.py")

    if os.path.isdir(GUIDES_DIR):
        for name in os.listdir(GUIDES_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(GUIDES_DIR, name)
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                disease_id = doc.get("disease_id")
                if disease_id:
                    guide_by_disease[disease_id] = doc
            except Exception as exc:
                print(f"   ⚠  Failed to load guide {name}: {exc}")
        print(f"   ✓  Care guides loaded ({len(guide_by_disease)}): "
              f"{', '.join(sorted(guide_by_disease)) or '—'}")


def _logo_url(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    # Only expose basename to avoid path traversal
    safe = os.path.basename(filename)
    return f"/static/hospital-logos/{safe}"


def _pick_lang(lang: str) -> str:
    return "zh" if (lang or "").lower().startswith("zh") else "en"


def _field(obj: Dict[str, Any], base: str, lang: str, fallback: str = "") -> str:
    """Pick bilingual field: prefer base_zh / base_en, else legacy `base`."""
    preferred = obj.get(f"{base}_{lang}")
    if preferred:
        return str(preferred)
    other = "en" if lang == "zh" else "zh"
    alt = obj.get(f"{base}_{other}")
    if alt:
        return str(alt)
    legacy = obj.get(base)
    return str(legacy) if legacy else fallback


def _list_field(obj: Dict[str, Any], base: str, lang: str) -> List[str]:
    preferred = obj.get(f"{base}_{lang}")
    if isinstance(preferred, list) and preferred:
        return [str(x) for x in preferred]
    other = "en" if lang == "zh" else "zh"
    alt = obj.get(f"{base}_{other}")
    if isinstance(alt, list) and alt:
        return [str(x) for x in alt]
    legacy = obj.get(base)
    if isinstance(legacy, list):
        return [str(x) for x in legacy]
    return []


def build_guide_response(disease_id: str, lang: str = "en") -> DiseaseGuideResponse:
    """Assemble a lang-isolated guide payload (en|zh)."""
    disease_id = disease_id.strip()
    lang = _pick_lang(lang)
    doc = guide_by_disease.get(disease_id)

    if not doc:
        name_en = disease_id
        name_zh = disease_dict_zh.get(disease_id, "")
        if graph is not None and graph.G.has_node(disease_id):
            name_en = graph.G.nodes[disease_id].get("name") or disease_id
        name = name_zh if lang == "zh" and name_zh else name_en
        name_alt = name_en if lang == "zh" else name_zh
        message = (
            "该病的就医指南仍在建设中。您可先查阅 Orphanet 或咨询罕见病协作网医院。"
            if lang == "zh"
            else "A curated care guide for this disease is still being prepared. "
                 "You can check Orphanet or a rare-disease collaborative network hospital."
        )
        return DiseaseGuideResponse(
            disease_id=disease_id,
            available=False,
            name=name,
            name_alt=name_alt or "",
            message=message,
        )

    name_zh = doc.get("name_zh") or ""
    name_en = doc.get("name_en") or ""
    primary = name_zh if lang == "zh" else name_en
    secondary = name_en if lang == "zh" else name_zh

    experts_out: List[GuideExpert] = []
    cities: set = set()
    for ex in doc.get("experts") or []:
        hosp = guide_hospitals.get(ex.get("hospital_id") or "")
        if not hosp:
            continue
        city_disp = _field(hosp, "city", lang)
        if city_disp:
            cities.add(city_disp)
        name_zh_hosp = hosp.get("name_zh") or hosp.get("name") or ""
        experts_out.append(
            GuideExpert(
                id=ex.get("id") or "",
                name=_field(ex, "name", lang),
                type=ex.get("type") or "doctor",
                bio=_field(ex, "bio", lang),
                hospital=GuideHospital(
                    id=hosp["id"],
                    name=_field(hosp, "name", lang),
                    city=city_disp,
                    map_query=name_zh_hosp,
                    lat=hosp.get("lat"),
                    lng=hosp.get("lng"),
                    logo_url=_logo_url(hosp.get("logo")),
                    advantage=_field(hosp, "advantage", lang),
                ),
            )
        )

    city_list = sorted(c for c in cities if c)
    return DiseaseGuideResponse(
        disease_id=doc.get("disease_id") or disease_id,
        available=True,
        name=primary or secondary or disease_id,
        name_alt=secondary if secondary and secondary != primary else "",
        summary=_field(doc, "summary", lang),
        care_tips=_list_field(doc, "care_tips", lang),
        specialty_keywords=_list_field(doc, "specialty_keywords", lang),
        experts=experts_out,
        cities=city_list,
    )


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load knowledge graph and FAISS index on startup.

    All NLP/embedding is now cloud-based (DeepSeek + SiliconFlow),
    so no local models need to be loaded. Server starts instantly.
    """
    global graph, vector_index, vector_mapping

    # Care guides are lightweight JSON — always load (even if graph was
    # pre-loaded by `_load_all_models` in `__main__`).
    load_care_guides()

    # Skip if already loaded (e.g. by _load_all_models in __main__)
    if graph is not None:
        print("   ℹ  Graph pre-loaded — skipping lifespan load\n")
        yield
        return

    print("\n" + "=" * 60)
    print(" 🧬  Rare Disease Pre-diagnosis Clue Finder — Starting up")
    print("     NLP: DeepSeek API  |  Embeddings: SiliconFlow (BAAI/bge-m3)")
    print("=" * 60)

    # 1. Load knowledge graph
    try:
        graph = DiseaseGraph()
        graph.load_all()
        stats = graph.stats()
        print(f"\n✅  Graph ready: {stats['total_nodes']:,} nodes, "
              f"{stats['total_edges']:,} edges", flush=True)
    except Exception as exc:
        print(f"❌  Fatal (graph): {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Load FAISS index (must be built with API-based embeddings, 1024-dim)
    try:
        if os.path.isfile(FAISS_INDEX_PATH) and os.path.isfile(FAISS_MAPPING_PATH):
            vector_index = faiss.read_index(FAISS_INDEX_PATH)
            with open(FAISS_MAPPING_PATH, "rb") as f:
                vector_mapping = pickle.load(f)
            print(f"   ✓  FAISS index loaded ({vector_index.ntotal:,} vectors, "
                  f"dim={vector_index.d})")
            if vector_index.d != EMBEDDING_DIM:
                print(f"   ⚠  FAISS index dim mismatch: index={vector_index.d}, "
                      f"expected={EMBEDDING_DIM}. Rebuild with "
                      f"build_vector_index.py")
        else:
            print(f"   ⚠  FAISS index not found — run build_vector_index.py first")
    except Exception as exc:
        print(f"   ⚠  FAISS index load failed: {exc}")

    # 3. Load layperson dictionary (optional — graceful fallback)
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

    # 3b. Load Chinese layperson dictionary (optional)
    try:
        if os.path.isfile(LAYERSON_DICT_ZH_PATH):
            with open(LAYERSON_DICT_ZH_PATH, encoding="utf-8") as f:
                layperson_dict_zh.update(json.load(f))
            uniq_zh = len(set(layperson_dict_zh.values()))
            print(f"   ✓  中文通俗字典加载 ({len(layperson_dict_zh):,} 条, {uniq_zh:,} 唯一值)")
            if uniq_zh < len(layperson_dict_zh):
                print(f"      ⚠  {len(layperson_dict_zh) - uniq_zh} 个重复 — 运行 build_layperson_dict_zh.py --resolve-only")
        else:
            print("   ⚠  layperson_dict_zh.json 未找到 — 中文模式将退化到英文")
            print("      运行 build_layperson_dict_zh.py 生成中文翻译")
    except Exception as exc:
        print(f"   ⚠  中文通俗字典加载失败: {exc}")

    # 3c. Load Chinese HPO names (optional)
    try:
        if os.path.isfile(HPO_NAMES_ZH_PATH):
            with open(HPO_NAMES_ZH_PATH, encoding="utf-8") as f:
                hpo_names_zh.update(json.load(f))
            print(f"   ✓  中文专业术语加载 ({len(hpo_names_zh):,} 条)")
        else:
            print("   ⚠  hpo_names_zh.json 未找到 — 中文专家模式将显示英文名")
            print("      运行 build_layperson_dict_zh.py --mode medical 生成")
    except Exception as exc:
        print(f"   ⚠  中文专业术语加载失败: {exc}")

    # 3d. Load Chinese disease names (optional)
    try:
        if os.path.isfile(DISEASE_DICT_ZH_PATH):
            with open(DISEASE_DICT_ZH_PATH, encoding="utf-8") as f:
                disease_dict_zh.update(json.load(f))
            print(f"   ✓  中文疾病名加载 ({len(disease_dict_zh):,} 条)")
        else:
            print("   ⚠  disease_names_zh.json 未找到 — 中文模式将显示英文疾病名")
            print("      运行 build_disease_dict_zh.py 生成中文翻译")
    except Exception as exc:
        print(f"   ⚠  中文疾病名加载失败: {exc}")

    print("=" * 60 + "\n")
    yield  # application runs here
    # Shutdown: nothing to clean up


# ---------------------------------------------------------------------------
# FastAPI application (must come after lifespan definition)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Rare Disease Pre-diagnosis Clue Finder",
    description="Knowledge Graph API — True Path Rule inference over HPO + Orphanet",
    version="0.3.0",
    lifespan=lifespan,
)

# ── CORS (allow Vite frontend dev server on :5173) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve Vite-built JS/CSS from frontend/dist/assets when present.
_assets_dir = os.path.join(FRONTEND_DIST_DIR, "assets")
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

# China GeoJSON for the care-guide ExpertMap (Vite puts it in dist/geo).
_geo_dir = os.path.join(FRONTEND_DIST_DIR, "geo")
if os.path.isdir(_geo_dir):
    app.mount("/geo", StaticFiles(directory=_geo_dir), name="geo")

# Hospital logos (filenames filled later via hospital_logo_map.json).
if os.path.isdir(HOSPITAL_LOGO_DIR):
    app.mount(
        "/static/hospital-logos",
        StaticFiles(directory=HOSPITAL_LOGO_DIR),
        name="hospital-logos",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _resolve_frontend_index() -> str:
    """Serve the React production build."""
    if os.path.isfile(FRONTEND_INDEX_PATH):
        return FRONTEND_INDEX_PATH
    raise HTTPException(
        status_code=404,
        detail="Frontend not found. Run `cd frontend && npm run build` first.",
    )


def _spa_html() -> FileResponse:
    return FileResponse(
        _resolve_frontend_index(),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/")
def serve_frontend():
    """Serve the patient-facing React SPA."""
    return _spa_html()


@app.get("/disease/{disease_id:path}")
def serve_disease_spa(disease_id: str):
    """SPA fallback so /disease/ORPHA:280 refreshes work in production."""
    return _spa_html()


@app.get("/api/health", response_model=HealthResponse)
def health_check():
    """Return server health and knowledge-graph statistics."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    return HealthResponse(status="ok", graph_stats=graph.stats())


@app.get("/api/guides/{disease_id:path}", response_model=DiseaseGuideResponse)
def get_disease_guide(
    disease_id: str,
    lang: str = Query("en", description="Language: 'en' or 'zh'"),
):
    """Return curated care-guide experts for a disease (WHS first).

    Payload is language-isolated: all display strings follow `lang`.
    Unknown diseases still return a placeholder with available=false.
    """
    # Accept both ORPHA:280 and URL-encoded forms; strip trailing junk.
    disease_id = unquote(disease_id).strip().rstrip("/")
    if not disease_id:
        raise HTTPException(status_code=400, detail="disease_id required")
    return build_guide_response(disease_id, lang=lang)


@app.post("/api/predict", response_model=PredictResponse)
def predict_diseases(req: PredictRequest, mode: str = "expert",
                     lang: str = Query("en", description="Language: 'en' or 'zh'")):
    """Run the True Path Rule inference and return the top-5 diseases.

    Each disease prediction includes explainable matched paths so the UI
    can show *why* a disease was suggested.

    Also returns `suggested_hpos` — the next best symptoms to ask about,
    selected for their discriminative power across the top-5 diseases.

    Query parameters:
        mode — "expert" (default) for original HPO names,
               "public" for layperson-translated names
        lang — "en" (default) or "zh" for Chinese translations
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")
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

    # --- Translate names based on mode & language ---
    if mode == "public":
        for s in suggestions:
            s.name = resolve_name(s.hpo_id, s.name, "public", lang)
            s.reason = s.reason  # keep reason as-is (refers to disease name)
        for disease in results:
            for path in disease.matched_paths:
                path.input_hpo_name = resolve_name(path.input_hpo_id, path.input_hpo_name, "public", lang)
                path.matched_hpo_name = resolve_name(path.matched_hpo_id, path.matched_hpo_name, "public", lang)
                path.explanation = path.explanation  # keep explanation structure
            for mh in disease.missing_critical_hpos:
                mh.name = resolve_name(mh.hpo_id, mh.name, "public", lang)
    elif lang == "zh":
        # Expert mode with Chinese: translate HPO names to Chinese medical terms
        for s in suggestions:
            s.name = resolve_name(s.hpo_id, s.name, "expert", lang)
        for disease in results:
            for path in disease.matched_paths:
                path.input_hpo_name = resolve_name(path.input_hpo_id, path.input_hpo_name, "expert", lang)
                path.matched_hpo_name = resolve_name(path.matched_hpo_id, path.matched_hpo_name, "expert", lang)
            for mh in disease.missing_critical_hpos:
                mh.name = resolve_name(mh.hpo_id, mh.name, "expert", lang)

    # --- Translate disease names for zh mode ---
    if lang == "zh":
        for disease in results:
            disease.disease_name = resolve_disease_name(disease.disease_id, disease.disease_name, lang)

    return PredictResponse(
        query_hpo_ids=req.hpo_ids,
        results=results,
        suggested_hpos=suggestions,
    )


@app.get("/api/hpo-search", response_model=HPOSearchResponse)
def search_hpo(q: str = "", limit: int = 20, mode: str = "expert",
               lang: str = Query("en", description="Language: 'en' or 'zh'")):
    """Search HPO phenotype terms by name (case-insensitive substring).

    Query parameters:
        q     — search string (min 2 characters)
        limit — max results (default 20, max 50)
        mode  — "expert" (default) for original HPO names,
                "public" for layperson-translated names
        lang  — "en" (default) or "zh" for Chinese translations
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    q = q.strip()
    if len(q) < 2:
        return HPOSearchResponse(query=q, results=[], total=0)
    limit = max(1, min(limit, 50))
    results = graph.search_hpo(q, limit=limit)

    # --- Chinese name search (when lang=zh and hpo_names_zh available) ---
    if lang == "zh" and hpo_names_zh:
        q_lower = q.lower()
        zh_scored: List[Tuple[int, str, str]] = []
        seen_ids: Set[str] = {r[0] for r in results}
        for hpo_id, zh_name in hpo_names_zh.items():
            if hpo_id in seen_ids:
                continue
            idx = zh_name.lower().find(q_lower)
            if idx != -1:
                zh_scored.append((idx, hpo_id, zh_name))
        zh_scored.sort(key=lambda x: (x[0], len(x[2])))
        for _, hpo_id, zh_name in zh_scored:
            if len(results) >= limit:
                break
            results.append((hpo_id, graph._hpo_id_to_name.get(hpo_id, zh_name)))

    return HPOSearchResponse(
        query=q,
        results=[HPOSearchResult(
            id=r[0],
            name=resolve_name(r[0], r[1], mode, lang)
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
def vector_search(text: str = "", k: int = 5, mode: str = "expert",
                  lang: str = Query("en", description="Language: 'en' or 'zh'")):
    """Semantic search over HPO terms using sentence-transformers embeddings.

    Encodes free-text layperson descriptions (e.g. "muscle stiffness in legs")
    and returns the top-k closest HPO terms by L2 distance in the FAISS index.

    Query parameters:
        text — free-text symptom description (min 3 characters)
        k    — number of results (default 5, max 10)
        mode — "expert" (default) for original HPO names,
               "public" for layperson-translated names
        lang — "en" (default) or "zh" for Chinese translations
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")
    if vector_index is None:
        raise HTTPException(
            status_code=503,
            detail="Vector search not available — FAISS index not loaded",
        )
    text = text.strip()
    if len(text) < 3:
        return VectorSearchResponse(query=text, results=[])

    k = max(1, min(k, 10))

    # Encode query via cloud Embedding API
    try:
        query_vec = get_api_embedding(text)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Embedding API unavailable: {e}",
        )

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
            name=resolve_name(entry["hpo_id"], entry["name"], mode, lang),
            score=round(float(distances[0][i]), 4),
        ))

    return VectorSearchResponse(query=text, results=results)


@app.get("/api/smart-search", response_model=SmartSearchResponse)
def smart_search(text: str = Query(..., min_length=3, description="Free-text symptom description"),
                 mode: str = Query("expert", description="Mode: 'expert' or 'public'"),
                 lang: str = Query("en", description="Language: 'en' or 'zh'")):
    """DeepSeek-powered segmentation + decoupled hybrid search.

    Pipeline:
    1. DeepSeek extracts symptoms → {raw_description, standard_term}
    2. Vector search uses ONLY standard_term (no semantic dilution from layperson words)
    3. Keyword search uses standard_term with elevated priority (lexical match > vector)
    4. UI displays raw_description; embedding/keyword operate on clean medical terms

    This separation eliminates the "semantic dilution" problem where mixing
    layperson words (hands, feet, pain) with medical terms drowns out the
    precise signal needed for HPO ontology matching.
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")
    if vector_index is None:
        raise HTTPException(
            status_code=503,
            detail="Smart search unavailable — FAISS index not loaded. "
                   "Run: python3 build_vector_index.py",
        )

    text = text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Query must be at least 3 characters")

    # 1. DeepSeek extraction → structured {raw, standard} dicts
    fragments = extract_symptom_fragments(text, lang)

    # 2. For each fragment: vector search on standard ONLY, keyword search on standard,
    #    display label from raw.
    groups: List[SmartSearchGroup] = []
    global_best: Dict[str, Tuple[float, str, float, str]] = {}  # hpo_id → (best_score, name, _kw_flag, hint)

    for frag_dict in fragments:
        raw_desc = frag_dict["raw"]       # for UI display
        std_term = frag_dict["standard"]   # for embedding + keyword search

        # 2a. Vector search — use PURE standard term (no raw_description pollution)
        vec_results: List[VectorSearchResult] = []
        try:
            query_vec = get_api_embedding(std_term)
            distances, indices = vector_index.search(query_vec, 6)
            for i in range(min(6, len(indices[0]))):
                idx = int(indices[0][i])
                if idx < 0 or idx >= len(vector_mapping):
                    continue
                entry = vector_mapping[idx]
                score = round(float(distances[0][i]), 4)
                vec_results.append(VectorSearchResult(
                    hpo_id=entry["hpo_id"],
                    name=resolve_name(entry["hpo_id"], entry["name"], mode, lang),
                    score=score,
                    hint=resolve_parent_hint(entry["hpo_id"]) if mode == "public" else "",
                ))
        except Exception:
            pass  # vector search failed for this fragment — skip

        # 2b. Keyword search — use standard term, ELEVATED priority
        #     Lexical match on precise medical vocabulary always beats vector similarity.
        kw_results: List[VectorSearchResult] = []
        is_bio = is_biomarker_fragment(std_term)
        kw_score = 2.0 if is_bio else 1.5  # always above vector scores (0.4-1.0)
        try:
            hpo_matches = graph.search_hpo(std_term, limit=6)
            for hpo_id, name in hpo_matches:
                hint = ""
                if mode == "public" and is_bio:
                    hint = "🧪 Lab"
                elif mode == "public":
                    hint = resolve_parent_hint(hpo_id)
                kw_results.append(VectorSearchResult(
                    hpo_id=hpo_id,
                    name=resolve_name(hpo_id, name, mode, lang),
                    score=kw_score,
                    hint=hint,
                ))
        except Exception:
            pass

        # 2c. Merge: keyword first (always wins), then vector (dedup within fragment)
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
                global_best[r.hpo_id] = (r.score, r.name, 1.0 if r.score >= kw_score else 0.0, r.hint)

        groups.append(SmartSearchGroup(
            fragment=raw_desc,  # display the user's original words
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
        fragments=[f["raw"] for f in fragments],
        groups=deduped_groups,
    )


# ── Per-fragment processing for auto_diagnose (parallel-safe) ────────────
def _process_one_fragment(
    frag_dict: Dict[str, str],
    full_text: str,
) -> Dict[str, Any]:
    """Process one symptom fragment: keyword + FAISS + DeepSeek rerank.

    Returns a dict with all the data needed to build AutoSelection + SymptomLog.
    This function is designed to be called from a ThreadPoolExecutor.
    """
    raw_desc = frag_dict["raw"]
    std_term = frag_dict["standard"]

    matched_hpo_id: Optional[str] = None
    matched_hpo_name: Optional[str] = None
    match_method: str = ""

    kw_candidates: List[Dict[str, str]] = []
    faiss_candidates: List[Dict[str, Any]] = []
    reranker_selection: Optional[str] = None
    reranker_rejected: bool = False

    candidates_for_llm: List[Dict[str, str]] = []
    seen_cand_ids: Set[str] = set()

    # a. Keyword candidates (fast, in-memory)
    try:
        kw_hits = graph.search_hpo(std_term, limit=5)
        for hpo_id, name in kw_hits:
            kw_candidates.append({"hpo_id": hpo_id, "name": name})
            if hpo_id not in seen_cand_ids:
                candidates_for_llm.append({"hpo_id": hpo_id, "name": name})
                seen_cand_ids.add(hpo_id)
    except Exception:
        pass

    # b. FAISS vector candidates
    if vector_index is not None:
        try:
            query_vec = get_api_embedding(std_term)
            distances, indices = vector_index.search(query_vec, 20)
            for i in range(min(20, len(indices[0]))):
                idx = int(indices[0][i])
                if idx < 0 or idx >= len(vector_mapping):
                    continue
                entry = vector_mapping[idx]
                score = float(distances[0][i])
                hpo_id = entry["hpo_id"]
                faiss_candidates.append({"hpo_id": hpo_id, "name": entry["name"], "score": round(score, 4)})
                if hpo_id not in seen_cand_ids:
                    candidates_for_llm.append({"hpo_id": hpo_id, "name": entry["name"]})
                    seen_cand_ids.add(hpo_id)
        except Exception:
            pass

    # c. DeepSeek rerank
    if candidates_for_llm:
        try:
            best_id = llm_rerank_candidates(raw_desc, candidates_for_llm, full_text=full_text)
            if best_id:
                reranker_selection = best_id
                best_candidate = next(
                    (c for c in candidates_for_llm if c["hpo_id"] == best_id),
                    None,
                )
                if best_candidate:
                    matched_hpo_id = best_id
                    matched_hpo_name = best_candidate["name"]
                    match_method = "llm_rerank"
                else:
                    reranker_rejected = False
            else:
                reranker_rejected = True
        except Exception:
            # DeepSeek API failed → keyword fallback
            if kw_candidates:
                best_kw = kw_candidates[0]
                matched_hpo_id = best_kw["hpo_id"]
                matched_hpo_name = best_kw["name"]
                match_method = "keyword_fallback"

    return {
        "raw_desc": raw_desc,
        "std_term": std_term,
        "matched_hpo_id": matched_hpo_id,
        "matched_hpo_name": matched_hpo_name,
        "match_method": match_method,
        "kw_candidates": kw_candidates,
        "faiss_candidates": faiss_candidates,
        "reranker_selection": reranker_selection,
        "reranker_rejected": reranker_rejected,
    }


@app.get("/api/auto-diagnose", response_model=PredictResponse)
def auto_diagnose(
    text: str = Query(..., min_length=3, description="Free-text symptom description"),
    mode: str = Query("public", description="Mode: 'expert' or 'public' (default: public)"),
    lang: str = Query("en", description="Language: 'en' or 'zh'"),
):
    """Public-mode zero-click diagnosis: free-text in, disease predictions out.

    Designed for non-expert users who cannot be expected to search and select
    HPO terms manually. The full pipeline runs automatically:

    1. DeepSeek extracts symptoms → {raw_description, standard_term}
    2. For each standard_term: keyword search first (lexical HPO match),
       vector search as fallback
    3. Auto-selects the best HPO per symptom, deduplicates
    4. Runs True Path Rule inference on the combined HPO set
    5. Returns top-5 diseases with matched paths + selection trace

    The response includes `auto_selections` so the user can see exactly
    which of their words mapped to which medical terms.
    """
    if mode not in ("expert", "public"):
        raise HTTPException(status_code=400, detail="mode must be 'expert' or 'public'")
    if lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not loaded yet")

    text = text.strip()
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Query must be at least 3 characters")

    # 1. Extract structured symptoms via DeepSeek
    fragments = extract_symptom_fragments(text, lang)
    if not fragments:
        raise HTTPException(status_code=400, detail="No symptoms could be extracted from the text")

    # 2. Auto-select HPOs: parallel processing per fragment
    selected_hpos: List[Tuple[str, str]] = []
    auto_selections: List[AutoSelection] = []
    symptom_logs: List[SymptomLog] = []
    seen_hpo_ids: Set[str] = set()

    # Process all fragments in parallel (FAISS + reranker are I/O-bound API calls)
    fragment_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(fragments))) as executor:
        futures = {
            executor.submit(_process_one_fragment, fd, text): i
            for i, fd in enumerate(fragments)
        }
        # Collect results in original order
        fragment_results = [None] * len(fragments)
        for future in as_completed(futures):
            idx = futures[future]
            try:
                fragment_results[idx] = future.result()
            except Exception:
                fragment_results[idx] = None

    for result in fragment_results:
        if result is None:
            continue
        raw_desc = result["raw_desc"]
        std_term = result["std_term"]
        matched_hpo_id = result["matched_hpo_id"]
        matched_hpo_name = result["matched_hpo_name"]
        match_method = result["match_method"]

        # Deduplicate: skip if this HPO was already selected for another fragment
        if matched_hpo_id and matched_hpo_id in seen_hpo_ids:
            matched_hpo_id = None
            matched_hpo_name = None
            match_method = "none"

        # Record the selection
        if matched_hpo_id:
            seen_hpo_ids.add(matched_hpo_id)
            selected_hpos.append((matched_hpo_id, matched_hpo_name))
            auto_selections.append(AutoSelection(
                raw_description=raw_desc,
                standard_term=std_term,
                matched_hpo_id=matched_hpo_id,
                matched_hpo_name=resolve_name(matched_hpo_id, matched_hpo_name, mode, lang),
                match_method=match_method,
            ))
        else:
            auto_selections.append(AutoSelection(
                raw_description=raw_desc,
                standard_term=std_term,
                matched_hpo_id="",
                matched_hpo_name="(no match found)",
                match_method="none",
            ))

        # Computation log for this symptom
        symptom_logs.append(SymptomLog(
            raw_description=raw_desc,
            standard_term=std_term,
            keyword_candidates=result["kw_candidates"],
            faiss_candidates=result["faiss_candidates"],
            reranker_selection=result.get("reranker_selection"),
            reranker_rejected=result.get("reranker_rejected", False),
            selected_hpo_id=matched_hpo_id or "",
            selected_hpo_name=matched_hpo_name or "",
            match_method=match_method,
        ))

    # 3. Must have at least one valid HPO to predict
    if not selected_hpos:
        return PredictResponse(
            query_hpo_ids=[],
            results=[],
            suggested_hpos=[],
            auto_selections=auto_selections,
        )

    # 4. Run True Path Rule inference
    hpo_ids = [hpo_id for hpo_id, _ in selected_hpos]
    try:
        results = graph.predict(hpo_ids)
        top_disease_ids = [r.disease_id for r in results]
        suggestions = graph.suggest_next_hpos(top_disease_ids, set(hpo_ids))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 4b. Build inference computation log
    total_diseases = max(1, len(graph._disease_id_to_name))
    disease_score_logs: List[DiseaseScoreLog] = []
    for disease in results:
        contrib_logs: List[ContributionLog] = []
        for path in disease.matched_paths:
            df = len(graph._hpo_to_diseases.get(path.matched_hpo_id, []))
            raw_idf = math.log(total_diseases / (df + 1))
            idf = 1.0 + math.sqrt(raw_idf)
            contrib_logs.append(ContributionLog(
                hpo_id=path.matched_hpo_id,
                hpo_name=path.matched_hpo_name,
                match_type=path.match_type,
                frequency_weight=path.frequency_weight,
                multiplier=path.score_multiplier,
                idf_weight=round(idf, 4),
                contribution=path.contribution,
            ))
        disease_score_logs.append(DiseaseScoreLog(
            disease_id=disease.disease_id,
            disease_name=resolve_disease_name(disease.disease_id, disease.disease_name, lang),
            total_score=disease.total_score,
            explained_ratio=disease.explained_ratio,
            contributions=contrib_logs,
        ))

    inference_log = InferenceLog(
        total_diseases=total_diseases,
        input_hpo_ids=hpo_ids,
        disease_scores=disease_score_logs,
    )

    computation_log = ComputationLog(
        extraction={"input_text": text, "fragments": fragments},
        symptoms=symptom_logs,
        inference=inference_log,
    )

    # 5. Translate names based on mode & language
    if mode == "public":
        for s in suggestions:
            s.name = resolve_name(s.hpo_id, s.name, "public", lang)
        for disease in results:
            for path in disease.matched_paths:
                path.input_hpo_name = resolve_name(path.input_hpo_id, path.input_hpo_name, "public", lang)
                path.matched_hpo_name = resolve_name(path.matched_hpo_id, path.matched_hpo_name, "public", lang)
            for mh in disease.missing_critical_hpos:
                mh.name = resolve_name(mh.hpo_id, mh.name, "public", lang)
    elif lang == "zh":
        # Expert mode with Chinese: translate HPO names to Chinese medical terms
        for s in suggestions:
            s.name = resolve_name(s.hpo_id, s.name, "expert", lang)
        for disease in results:
            for path in disease.matched_paths:
                path.input_hpo_name = resolve_name(path.input_hpo_id, path.input_hpo_name, "expert", lang)
                path.matched_hpo_name = resolve_name(path.matched_hpo_id, path.matched_hpo_name, "expert", lang)
            for mh in disease.missing_critical_hpos:
                mh.name = resolve_name(mh.hpo_id, mh.name, "expert", lang)

    # --- Translate disease names for zh mode ---
    if lang == "zh":
        for disease in results:
            disease.disease_name = resolve_disease_name(disease.disease_id, disease.disease_name, lang)

    return PredictResponse(
        query_hpo_ids=hpo_ids,
        results=results,
        suggested_hpos=suggestions,
        auto_selections=auto_selections,
        computation_log=computation_log,
    )


# ---------------------------------------------------------------------------
# Phase 15: Missing-symptom layperson descriptions
# ---------------------------------------------------------------------------

# English prompt template — concise: only describe missing symptoms, no disease intro
_MISSING_DESC_PROMPT_EN = (
    "You are a medical translator. Convert the following medical symptom names "
    "into plain, everyday language that any patient can understand.\n\n"
    "Symptoms to translate:\n"
    "{missing_list}\n\n"
    "Rules:\n"
    "1. ONLY translate symptoms a patient can notice in daily life (visible, "
    "felt, experienced) — skin changes, pain, bodily sensations, etc.\n"
    "2. SKIP lab results, blood tests, enzyme levels, biomarker elevations, "
    "imaging findings — these cannot be self-perceived. If all are lab markers, "
    "output a single \"-\".\n"
    "3. Use everyday words — e.g. \"Angiokeratoma\" → \"small dark red dots "
    "on the skin (like tiny bumps), more visible after a hot shower\"\n"
    "4. Do NOT mention the disease name. Do NOT explain the disease. "
    "Do NOT recap the patient's own symptoms.\n"
    "5. Output a single sentence, symptoms separated by commas or semicolons. "
    "No markdown, no labels, no bullet points.\n"
    "6. Keep it under 50 words."
)

# Chinese prompt template (uses triple quotes to avoid escaping Chinese quotation marks)
_MISSING_DESC_PROMPT_ZH = """\
你是一位医学翻译，把下面的医学术语翻译成通俗大白话，让普通患者能看懂。

需要翻译的症状：
{missing_list}

要求：
1. 只翻译患者在生活中能自己察觉到的症状（看得见、摸得着、感觉得到的）——皮肤变化、疼痛、身体感觉等。
2. 跳过实验室指标、抽血化验结果、酶活性、影像检查发现——这些患者无法自己感知，不要写。
3. 如果所有症状都是化验指标、没有患者能感知的，输出一个\"-\"。
4. 用日常词汇——例如：「血管角化瘤」→「皮肤上出现红色或暗红色的小点点（像小疙瘩），洗完热水澡或运动后更明显」
5. 不要提疾病名称，不要解释这是什么病，不要重复患者已有的症状。
6. 输出一句连贯的话，用逗号或分号连接。不要 markdown、不要标签、不要编号。
7. 控制在 60 字以内。\
"""


def _build_batch_describe_prompt(predictions: List[DiseasePredictionSummary], lang: str) -> str:
    """Build a single batch prompt for translating missing symptoms across all diseases."""
    tasks = []
    for p in predictions:
        if p.missing_hpo_names:
            items = ", ".join(p.missing_hpo_names)
            tasks.append(f"ID:{p.disease_id} | {p.disease_name} | [{items}]")

    tasks_joined = "\n".join(tasks)

    if lang == "zh":
        return f"""你是一位医学翻译，把医学术语翻译成通俗大白话，让普通患者能看懂。

以下是候选疾病缺失的关键症状：
{tasks_joined}

任务：为每个疾病，把缺失症状翻译成一句连贯的白话描述。

要求：
1. 只翻译患者在生活中能自己察觉到的症状（看得见、摸得着、感觉得到的）——皮肤变化、疼痛、身体感觉等。
2. 跳过实验室指标、抽血化验结果、酶活性、影像检查发现——这些患者无法自己感知，跳过即可。如果全部都是化验指标，该疾病输出 "-"。
3. 不要提疾病名称，不要解释这是什么病，不要重复患者已有的症状。
4. 一句连贯的话，用逗号或分号连接。控制在 60 字以内。

输出严格的 JSON 对象，键为疾病 ID，值为翻译文本。不要用 markdown 代码块包裹。

示例输出格式：
{{"ORPHA:324": "皮肤上出现红色小点点，洗完热水澡后更明显", "ORPHA:1652": "眼睛怕光，经常流泪"}}"""
    else:
        return f"""You are a medical translator. Convert medical terms into plain, everyday language for patients.

Missing clinical terms per candidate disease:
{tasks_joined}

TASK: For each disease ID, translate its missing terms into ONE short, fluent sentence.

Rules:
1. ONLY describe signs a patient can self-perceive (visible, felt, experienced) — skin changes, pain, bodily sensations.
2. SKIP lab results, blood tests, enzyme levels, biomarker elevations, imaging — if ALL terms are lab markers, output "-".
3. Do NOT mention or explain the disease itself.
4. Keep it under 40 words per sentence.

Output a strict JSON object mapping disease_id to translated text. No markdown blocks.

Example output:
{{"ORPHA:324": "small dark red dots on skin, more visible after a hot shower", "ORPHA:1652": "eyes sensitive to light, frequent tearing"}}"""


@app.post("/api/describe-missing", response_model=DescribeMissingResponse)
def describe_missing_symptoms(req: DescribeMissingRequest):
    """Generate plain-language descriptions of missing symptoms for top-5 diseases.

    Uses a single batched DeepSeek call (not 5 parallel calls) to avoid
    rate-limit issues and reduce round-trip latency.
    """
    if not req.predictions:
        return DescribeMissingResponse(descriptions=[])
    if req.lang not in ("en", "zh"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'zh'")

    predictions = req.predictions[:5]

    # Check if there are any missing symptoms at all
    has_any_missing = any(p.missing_hpo_names for p in predictions)
    if not has_any_missing:
        return DescribeMissingResponse(descriptions=[
            DiseaseDescription(
                disease_id=p.disease_id,
                disease_name=p.disease_name,
                description="-",
            ) for p in predictions
        ])

    # Single batched DeepSeek call
    prompt = _build_batch_describe_prompt(predictions, req.lang)
    system_prompt = (
        "You are a strict clinical JSON translator. "
        "Output ONLY a valid JSON object, no markdown, no extra text."
    )

    translated_json: Dict[str, str] = {}
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()
        # DeepSeek sometimes wraps JSON in markdown fences or adds trailing noise
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        translated_json = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"Batch describe-missing failed: {e}", flush=True)
        translated_json = {}

    # Assemble results
    descriptions = []
    for p in predictions:
        desc = translated_json.get(p.disease_id, "")
        desc = desc.strip() if desc else ""

        if desc and desc != "-":
            # DeepSeek returned a valid perceptible-symptom description
            pass
        elif p.missing_hpo_names:
            # Fallback: no DeepSeek or all-lab → show raw names (better than hidden)
            sep = "、" if req.lang == "zh" else ", "
            desc = sep.join(p.missing_hpo_names)
        else:
            desc = "-"

        descriptions.append(DiseaseDescription(
            disease_id=p.disease_id,
            disease_name=p.disease_name,
            description=desc,
        ))

    return DescribeMissingResponse(descriptions=descriptions)


# ---------------------------------------------------------------------------
# Main (for direct execution)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # NOTE: Must use app instance (not "main:app" string) when running as __main__.
    # The string form causes uvicorn to create a second "main" module import,
    # which means lifespan-loaded globals (graph, vector_index) end up
    # in a different module than the one handling requests → 503 on all endpoints.
    # For reload support, run: python3 -m uvicorn main:app --reload
    uvicorn.run(app, host="0.0.0.0", port=8000)
