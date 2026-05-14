#!/usr/bin/env python3
"""
parse_data.py — Phase 1: Data Parsing & Graph Preparation
===========================================================
Parses 3 raw ontology/epidemiology files and produces 3 clean CSV files:

Inputs (expected in the same directory as this script):
  1. hp.json              — Human Phenotype Ontology (JSON-LD)
  2. ORDO_en_4.8.owl.xml  — Orphanet Rare Disease Ontology (OWL/RDF)
  3. en_product4.xml      — Orphanet epidemiological data (disease ↔ HPO links)

Outputs:
  - nodes.csv             (id, name, type, synonyms)
  - edges_hpo.csv         (child_id, parent_id, type)
  - edges_disease_hpo.csv (disease_id, hpo_id, frequency_weight)

Usage:
  python parse_data.py
  python parse_data.py --data-dir /path/to/data --output-dir /path/to/output
"""

import argparse
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants & Frequency Mapping
# ---------------------------------------------------------------------------

# Map Orphanet frequency strings → numerical weight [0, 1]
FREQUENCY_MAP: Dict[str, float] = {
    "Obligate (100%)":                  1.00,
    "Very frequent (99-80%)":           0.90,
    "Frequent (79-30%)":                0.55,
    "Occasional (29-5%)":               0.17,
    "Very rare (<4-1%)":                0.02,
    "Excluded (0%)":                    0.00,   # present in the data; explicitly zero
}
FREQUENCY_DEFAULT = 0.50  # fallback for missing / unrecognised frequency text

# Regex to convert HPO full IRI → compact ID (e.g. HP_0000118 → HP:0000118)
HPO_IRI_RE = re.compile(r'HP_(\d+)$')

# Regex to convert Orphanet full IRI → compact ID (e.g. Orphanet_123 → ORPHA:123)
ORPHA_IRI_RE = re.compile(r'Orphanet_(\d+)$')


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def hpo_compact(iri: str) -> str:
    """Convert a full HPO IRI to the compact 'HP:NNNNNNN' form.

    Example:
        http://purl.obolibrary.org/obo/HP_0000118  →  HP:0000118
    """
    m = HPO_IRI_RE.search(iri)
    if m:
        return f"HP:{m.group(1)}"
    raise ValueError(f"Cannot extract HPO ID from IRI: {iri}")


def orpha_compact(iri: str) -> str:
    """Convert a full Orphanet IRI to the compact 'ORPHA:NNNN' form.

    Example:
        http://www.orpha.net/ORDO/Orphanet_58  →  ORPHA:58
    """
    m = ORPHA_IRI_RE.search(iri)
    if m:
        return f"ORPHA:{m.group(1)}"
    raise ValueError(f"Cannot extract ORPHA ID from IRI: {iri}")


def frequency_to_weight(text: str) -> float:
    """Map an Orphanet HPOFrequency name to a numerical weight.

    Strips whitespace before lookup; falls back to FREQUENCY_DEFAULT for
    unrecognised or missing values.
    """
    if not text:
        return FREQUENCY_DEFAULT
    text = text.strip()
    return FREQUENCY_MAP.get(text, FREQUENCY_DEFAULT)


def write_csv(path: str, header: List[str], rows: List[List[str]]) -> None:
    """Write rows to a UTF-8 CSV file with the given header."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  ✓  Wrote {len(rows):,} rows → {path}")


# ---------------------------------------------------------------------------
# Parser 1: HPO (hp.json)
# ---------------------------------------------------------------------------

def parse_hp_json(path: str) -> Tuple[List[dict], List[dict]]:
    """Parse the Human Phenotype Ontology JSON file.

    Returns
    -------
    nodes : list of dict
        Each dict has keys 'id', 'name', 'type', 'synonyms'.
    edges : list of dict
        Each dict has keys 'child_id', 'parent_id', 'type'.
        Only 'is_a' edges are extracted (the ontology backbone).
    """
    print(f"\n📖 Parsing HPO: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    graph = data["graphs"][0]
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])

    print(f"   Raw nodes: {len(raw_nodes):,}  |  Raw edges: {len(raw_edges):,}")

    # --- Parse nodes ---
    nodes: List[dict] = []
    skip_count = 0
    for node in raw_nodes:
        try:
            node_id = hpo_compact(node["id"])
        except (ValueError, KeyError):
            skip_count += 1
            continue

        name = node.get("lbl", "")
        if not name:
            name = node_id  # fallback: use ID as label

        # Collect all synonym strings (val field) from the synonyms array
        synonyms: List[str] = []
        for syn in node.get("meta", {}).get("synonyms", []):
            val = syn.get("val", "")
            if val and val != name:  # don't duplicate the primary label
                synonyms.append(val)
        synonyms_str = " | ".join(synonyms)  # pipe-delimited in CSV

        nodes.append({
            "id": node_id,
            "name": name,
            "type": "Phenotype",
            "synonyms": synonyms_str,
        })

    if skip_count:
        print(f"   ⚠  Skipped {skip_count} nodes with unparseable IDs")

    # --- Parse edges (is_a only) ---
    edges: List[dict] = []
    skip_edges = 0
    for edge in raw_edges:
        pred = edge.get("pred", "")
        if pred != "is_a":
            continue  # skip other relation types for now
        try:
            child_id = hpo_compact(edge["sub"])
            parent_id = hpo_compact(edge["obj"])
        except (ValueError, KeyError):
            skip_edges += 1
            continue
        edges.append({
            "child_id": child_id,
            "parent_id": parent_id,
            "type": "is_a",
        })

    if skip_edges:
        print(f"   ⚠  Skipped {skip_edges} edges with unparseable IDs")

    print(f"   Parsed: {len(nodes):,} HPO nodes, {len(edges):,} is_a edges")
    return nodes, edges


# ---------------------------------------------------------------------------
# Parser 2: Orphanet Disease Ontology (ORDO_en_4.8.owl.xml)
# ---------------------------------------------------------------------------

def parse_ordo_owl(path: str) -> List[dict]:
    """Parse the Orphanet Rare Disease Ontology OWL/RDF XML.

    Extracts every owl:Class whose rdf:about IRI contains 'Orphanet_'.
    For each class, reads the rdfs:label as the disease name.

    Returns
    -------
    nodes : list of dict
        Each dict has keys 'id', 'name', 'type', 'synonyms'.
        'type' is always 'Disease'; 'synonyms' is always empty.
    """
    print(f"\n📖 Parsing ORDO: {path}")

    # Note: ORDO XML uses extensive namespaces, but ET with default namespace
    # handling works because the RDF/OWL namespace is the default (xmlns="...").
    # We register the OWL namespace as default so XPath queries work.
    tree = ET.parse(path)
    root = tree.getroot()

    # Build namespace map from the root element attributes
    ns = {
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    }

    nodes: List[dict] = []
    skip_count = 0

    for cls in root.findall("owl:Class", ns):
        about = cls.get(f"{{{ns['rdf']}}}about")
        if not about:
            continue

        # Only process classes that look like Orphanet entries
        if "Orphanet_" not in about:
            continue

        try:
            disease_id = orpha_compact(about)
        except ValueError:
            skip_count += 1
            continue

        # Get the rdfs:label (disease name)
        label_el = cls.find("rdfs:label", ns)
        name = label_el.text.strip() if label_el is not None and label_el.text else disease_id

        nodes.append({
            "id": disease_id,
            "name": name,
            "type": "Disease",
            "synonyms": "",  # ORDO classes don't carry synonyms in this file
        })

    if skip_count:
        print(f"   ⚠  Skipped {skip_count} classes with unparseable IDs")
    print(f"   Parsed: {len(nodes):,} Disease nodes")
    return nodes


# ---------------------------------------------------------------------------
# Parser 3: Orphanet Epidemiological Data (en_product4.xml)
# ---------------------------------------------------------------------------

def parse_en_product4(path: str) -> List[dict]:
    """Parse the Orphanet epidemiological product XML.

    For each <Disorder>, reads <OrphaCode> and then iterates over
    <HPODisorderAssociation> children to extract:
      - HPOId       (e.g. HP:0000118)
      - HPOFrequency → Name → frequency_weight

    Returns
    -------
    edges : list of dict
        Each dict has keys 'disease_id', 'hpo_id', 'frequency_weight'.
        These are Disease↔Phenotype links with a confidence weight.
    """
    print(f"\n📖 Parsing en_product4: {path}")

    tree = ET.parse(path)
    root = tree.getroot()

    edges: List[dict] = []
    disorders_parsed = 0
    associations_parsed = 0
    missing_hpo_id = 0
    missing_freq = 0

    for disorder in root.iter("Disorder"):
        orpha_el = disorder.find("OrphaCode")
        if orpha_el is None or not orpha_el.text:
            continue  # skip if no OrphaCode (shouldn't happen, but be safe)

        disease_id = f"ORPHA:{orpha_el.text.strip()}"
        disorders_parsed += 1

        for assoc in disorder.iter("HPODisorderAssociation"):
            hpo_el = assoc.find("HPO/HPOId")
            if hpo_el is None or not hpo_el.text:
                missing_hpo_id += 1
                continue

            hpo_id = hpo_el.text.strip()

            freq_el = assoc.find("HPOFrequency/Name")
            freq_text = freq_el.text if freq_el is not None else ""
            if not freq_text:
                missing_freq += 1

            weight = frequency_to_weight(freq_text)
            associations_parsed += 1

            edges.append({
                "disease_id": disease_id,
                "hpo_id": hpo_id,
                "frequency_weight": round(weight, 3),
            })

    print(f"   Disorders: {disorders_parsed:,}")
    print(f"   Disease↔HPO associations: {associations_parsed:,}")
    if missing_hpo_id:
        print(f"   ⚠  Skipped {missing_hpo_id} associations with missing HPOId")
    if missing_freq:
        print(f"   ⚠  {missing_freq} associations had missing frequency (defaulted to {FREQUENCY_DEFAULT})")

    return edges


# ---------------------------------------------------------------------------
# Data integration: deduplicate & merge nodes
# ---------------------------------------------------------------------------

def merge_nodes(hpo_nodes: List[dict], disease_nodes: List[dict]) -> List[List[str]]:
    """Combine HPO and Disease node dictionaries into a single deduplicated list.

    Deduplication: if the same ID appears in both sets (unlikely but possible),
    the later entry (disease) is dropped in favour of the earlier one.
    Returns rows ready for CSV writing.
    """
    seen: Set[str] = set()
    rows: List[List[str]] = []

    for node_list in (hpo_nodes, disease_nodes):
        for n in node_list:
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            rows.append([n["id"], n["name"], n["type"], n["synonyms"]])

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Parse raw ontology files → clean CSV graph data"
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory containing hp.json, ORDO_en_4.8.owl.xml, en_product4.xml",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for output CSVs (defaults to --data-dir)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir if args.output_dir else data_dir

    # Validate input files exist
    files_required = {
        "hp.json":            os.path.join(data_dir, "hp.json"),
        "ORDO owl":           os.path.join(data_dir, "ORDO_en_4.8.owl.xml"),
        "en_product4":        os.path.join(data_dir, "en_product4.xml"),
    }
    for label, fpath in files_required.items():
        if not os.path.isfile(fpath):
            print(f"❌  Missing required file: {label} ({fpath})", file=sys.stderr)
            sys.exit(1)

    print("=" * 60)
    print(" Phase 1: Data Parsing & Graph Preparation")
    print("=" * 60)

    # 1. Parse HPO
    hpo_nodes, hpo_edges = parse_hp_json(files_required["hp.json"])

    # 2. Parse ORDO
    disease_nodes = parse_ordo_owl(files_required["ORDO owl"])

    # 3. Parse en_product4
    disease_hpo_edges = parse_en_product4(files_required["en_product4"])

    # --- Write outputs ---
    print(f"\n💾 Writing CSVs to {output_dir}/")

    # nodes.csv
    nodes_rows = merge_nodes(hpo_nodes, disease_nodes)
    write_csv(
        os.path.join(output_dir, "nodes.csv"),
        header=["id", "name", "type", "synonyms"],
        rows=nodes_rows,
    )

    # edges_hpo.csv
    hpo_edge_rows = [[e["child_id"], e["parent_id"], e["type"]] for e in hpo_edges]
    write_csv(
        os.path.join(output_dir, "edges_hpo.csv"),
        header=["child_id", "parent_id", "type"],
        rows=hpo_edge_rows,
    )

    # edges_disease_hpo.csv
    dh_edge_rows = [[e["disease_id"], e["hpo_id"], str(e["frequency_weight"])]
                    for e in disease_hpo_edges]
    write_csv(
        os.path.join(output_dir, "edges_disease_hpo.csv"),
        header=["disease_id", "hpo_id", "frequency_weight"],
        rows=dh_edge_rows,
    )

    # --- Summary ---
    print("\n" + "=" * 60)
    print(" ✅  Phase 1 complete!")
    print(f"     nodes.csv:          {len(nodes_rows):,} nodes ({len(hpo_nodes):,} Phenotype + {len(disease_nodes):,} Disease)")
    print(f"     edges_hpo.csv:      {len(hpo_edges):,} HPO hierarchy edges")
    print(f"     edges_disease_hpo.csv: {len(disease_hpo_edges):,} Disease→Phenotype links")
    print("=" * 60)


if __name__ == "__main__":
    main()
