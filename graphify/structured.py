"""Opt-in structured (JSON) output for the read verbs: query, explain, path, affected, god-nodes.

Default text output is untouched. A global ``--format json`` placed before the verb makes the
verb print one JSON document instead; the document is built from the SAME values the text
renderer used (the renderers fill a ``capture`` dict, they do not recompute), so the two
cannot diverge.

Argument grammar (also usable with the default text format)::

    graphify [--graph PATH] [--format text|json] <verb> [verb options] [-- <literal values>...]

Everything after ``--`` is a literal positional value (query text / node label), never an
option, so a value such as ``--graph=/other`` cannot move the graph path. ``--version-json``
prints ``{"version", "patch_id"}``.
"""
from __future__ import annotations

import json
import sys

PATCH_ID = "kgs-k3-structured-output-1"
SCHEMA = "graphify-structured-read/1"

READ_VERBS = ("query", "explain", "path", "affected", "god-nodes", "god_nodes")
_ARITY = {"query": 1, "explain": 1, "affected": 1, "path": 2, "god-nodes": 0, "god_nodes": 0}
# Options a verb accepts before ``--`` (value-taking). Anything else is a usage error in
# the literal-positional form, so a typo never silently becomes a positional.
_VERB_OPTIONS = {
    "query": {"--budget", "--context"},
    "explain": set(),
    "path": set(),
    "affected": {"--relation", "--depth"},
    "god-nodes": {"--top"},
    "god_nodes": {"--top"},
}
_VERB_SWITCHES = {"query": {"--dfs"}, "path": {"--directed", "--undirected"}}

format_json = False
literal_count = 0  # positional values consumed from the ``--`` form (they sit at argv[2:2+n])


class UsageError(Exception):
    pass


def hoist_global_flags(argv: list[str]) -> list[str]:
    """Rewrite ``[prog, <globals>, verb, opts..., --, values...]`` to the legacy argv shape.

    Returns ``argv`` unchanged (same list) unless a global flag or a ``--`` separator is
    present, so every pre-existing invocation keeps its exact behaviour. The rewritten argv
    is ``[prog, verb, *values, *opts, --graph PATH]``: values sit at fixed slots the legacy
    handlers never scan for flags.
    """
    global format_json, literal_count
    prog, rest = argv[0], argv[1:]
    graph = None
    fmt = None
    i = 0
    saw_global = False
    while i < len(rest) and rest[i].startswith("-") and rest[i] not in READ_VERBS:
        tok = rest[i]
        if tok in ("--graph", "--format"):
            if i + 1 >= len(rest):
                raise UsageError(f"{tok} needs a value")
            val = rest[i + 1]
            i += 2
        elif tok.startswith("--graph=") or tok.startswith("--format="):
            tok, val = tok.split("=", 1)
            i += 1
        else:
            break
        saw_global = True
        if tok == "--graph":
            graph = val
        else:
            if val not in ("text", "json"):
                raise UsageError("--format must be text or json")
            fmt = val
    verb_tail = rest[i:]
    has_sep = bool(verb_tail) and verb_tail[0] in READ_VERBS and "--" in verb_tail[1:]
    if not saw_global and not has_sep:
        return argv
    if not verb_tail or verb_tail[0] not in READ_VERBS:
        raise UsageError("global --graph/--format flags apply to the read verbs only")
    verb, tail = verb_tail[0], verb_tail[1:]
    if "--" in tail:
        sep = tail.index("--")
        opts, values = tail[:sep], tail[sep + 1:]
        _check_opts(verb, opts)
        if len(values) != _ARITY[verb]:
            raise UsageError(f"{verb} takes {_ARITY[verb]} value(s) after --, got {len(values)}")
    else:
        opts, values = [], []
        # No separator: legacy positional form; leave the verb tail exactly as given.
        values_and_opts = tail
    new = [prog, verb]
    if "--" in tail:
        new += values + opts
        literal_count = len(values)
    else:
        new += values_and_opts
    if graph is not None:
        new += ["--graph", graph]
    if fmt == "json":
        format_json = True
    return new


def _check_opts(verb: str, opts: list[str]) -> None:
    valued, switches = _VERB_OPTIONS[verb], _VERB_SWITCHES.get(verb, set())
    j = 0
    while j < len(opts):
        tok = opts[j]
        name = tok.split("=", 1)[0]
        if name in valued:
            j += 1 if "=" in tok else 2
        elif tok in switches:
            j += 1
        else:
            raise UsageError(f"unsupported option before --: {tok}")
    if j > len(opts):
        raise UsageError(f"option {opts[-1]} needs a value")


def version_json() -> str:
    from graphify.__main__ import __version__
    return json.dumps({"version": __version__, "patch_id": PATCH_ID})


# ---------------------------------------------------------------- record builders

def _clean(value) -> str:
    from graphify.security import sanitize_label
    return sanitize_label(str(value if value is not None else ""))


def node_record(G, nid, **extra) -> dict:
    """One node as the structured document reports it (labels sanitized like the text output)."""
    d = G.nodes[nid]
    rec = {
        "id": str(nid),
        "label": _clean(d.get("label", nid)),
        "source_file": _clean(d.get("source_file", "")),
        "source_location": _clean(d.get("source_location", "")),
        "community": _clean(d.get("community_name") or d.get("community", "")),
        "degree": int(G.degree(nid)),
    }
    rec.update(extra)
    return rec


def edge_record(source, target, data: dict, **extra) -> dict:
    rec = {
        "source": str(source),
        "target": str(target),
        "relation": _clean(data.get("relation", "")),
        "confidence": _clean(data.get("confidence", "")),
        "source_file": _clean(data.get("source_file", "")),
        "source_location": _clean(data.get("source_location", "")),
    }
    rec.update(extra)
    return rec


def candidate_record(G, nid, role=None) -> dict:
    d = G.nodes[nid]
    rec = {"id": str(nid), "label": _clean(d.get("label", nid)), "source_file": _clean(d.get("source_file", ""))}
    if role:
        rec["role"] = role
    return rec


def document(verb, graph_path, *, nodes, edges, truncated, shown, total, token_budget, selection) -> dict:
    return {
        "schema": SCHEMA,
        "verb": verb,
        "graph_path": str(graph_path),
        "nodes": nodes,
        "edges": edges,
        "truncated": bool(truncated),
        "shown": shown,
        "total": total,
        "token_budget": token_budget,
        "selection": selection,
    }


def select(status, candidates=None, reason=None) -> dict:
    sel = {"status": status, "candidates": candidates or []}
    if reason:
        sel["reason"] = reason
    return sel


def emit(doc: dict) -> None:
    print(json.dumps(doc, ensure_ascii=False))


def empty(verb, graph_path, status, candidates=None, reason=None, token_budget=None) -> dict:
    return document(verb, graph_path, nodes=[], edges=[], truncated=False, shown=0, total=0,
                    token_budget=token_budget, selection=select(status, candidates, reason))


def query_document(graph_path, capture, token_budget) -> dict:
    """Document for ``query`` from what ``_query_graph_text`` / ``_subgraph_to_text`` captured."""
    G = capture.get("graph")
    seeds = capture.get("seeds", [])
    if not seeds:
        return empty("query", graph_path, "no_match", token_budget=token_budget)
    base = capture["base_graph"]
    sel = select("matched", [candidate_record(base, n, "seed") for n in seeds])
    nodes = [node_record(G, n, **({"role": "seed"} if n in seeds else {})) for n in capture["nodes"]]
    edges = [edge_record(s, t, d, **({"context": _clean(d["context"])} if d.get("context") else {}))
             for s, t, d in capture["edges"]]
    doc = document("query", graph_path, nodes=nodes, edges=edges, truncated=capture["truncated"],
                   shown=capture["shown"], total=capture["total"], token_budget=token_budget, selection=sel)
    doc["traversal"] = {"mode": capture["mode"], "depth": capture["depth"], "context": capture["context"]}
    if capture.get("over_budget"):
        doc["over_budget"] = True
    return doc


def affected_document(graph, graph_path, seed, hits, *, depth, relations) -> dict:
    """Seed first, then every hit in the order `affected_nodes` returned; each hit's edge points
    from the affected node to the node it was reached from (the reverse-traversal direction).
    Intermediate member nodes an edge points at that are not hits are appended with role "via"."""
    nodes = [node_record(graph, seed, role="seed")]
    present = {seed}
    edges = []
    for hit in hits:
        nodes.append(node_record(graph, hit.node_id, role="hit", depth=hit.depth))
        present.add(hit.node_id)
    for hit in hits:
        data = {"relation": hit.via_relation, "confidence": hit.via_confidence,
                "source_file": hit.via_file, "source_location": hit.via_location}
        target = hit.via_target or seed
        edges.append(edge_record(hit.node_id, target, data))
        if target not in present:
            nodes.append(node_record(graph, target, role="via"))
            present.add(target)
    doc = document("affected", graph_path, nodes=nodes, edges=edges, truncated=False,
                   shown=len(hits), total=len(hits), token_budget=None,
                   selection=select("matched", [candidate_record(graph, seed, "seed")]))
    doc["traversal"] = {"depth": depth, "relations": list(relations)}
    return doc


def explain_document(G, graph_path, nid, connections, *, shown_limit) -> dict:
    """The explained node first, then up to `shown_limit` connection neighbours (de-duplicated by id,
    in the printed order); one edge per printed connection, oriented by its true direction."""
    nodes = [node_record(G, nid, role="subject")]
    present = {nid}
    edges = []
    for direction, nb, edata in connections[:shown_limit]:
        edges.append(edge_record(nid if direction == "out" else nb, nb if direction == "out" else nid, edata))
        if nb not in present:
            nodes.append(node_record(G, nb, role="neighbor"))
            present.add(nb)
    total = len(connections)
    return document("explain", graph_path, nodes=nodes, edges=edges, truncated=total > shown_limit,
                    shown=min(total, shown_limit), total=total, token_budget=None,
                    selection=select("matched", [candidate_record(G, nid, "subject")]))


def explain_ambiguous_document(G, graph_path, rivals) -> dict:
    doc = document("explain", graph_path, nodes=[node_record(G, n, role="candidate") for n in rivals],
                   edges=[], truncated=False, shown=len(rivals), total=len(rivals), token_budget=None,
                   selection=select("ambiguous", [candidate_record(G, n) for n in rivals]))
    return doc


def path_document(G, graph_path, path_nodes, hops, src_nid, tgt_nid, rivals) -> dict:
    """`hops` is a list of (u, v, forward, datas) exactly as the text renderer walked them."""
    nodes = [node_record(G, n) for n in path_nodes]
    edges = []
    for u, v, forward, datas in hops:
        rels = sorted({d.get("relation") for d in datas if d.get("relation")})
        confs = sorted({d.get("confidence") for d in datas if d.get("confidence")})
        first = datas[0] if datas else {}
        edge = edge_record(u if forward else v, v if forward else u,
                           {"relation": "/".join(rels) if rels else "related",
                            "confidence": "/".join(confs),
                            "source_file": first.get("source_file", ""),
                            "source_location": first.get("source_location", "")})
        edges.append(edge)
    cands = [candidate_record(G, src_nid, "source"), candidate_record(G, tgt_nid, "target")]
    cands += [candidate_record(G, n, role) for role, n in rivals]
    return document("path", graph_path, nodes=nodes, edges=edges, truncated=False,
                    shown=len(edges), total=len(edges), token_budget=None,
                    selection=select("matched", cands, "close_runner_up" if rivals else None))


def god_nodes_document(G, graph_path, gods) -> dict:
    nodes = [dict(node_record(G, g["id"]), role="god", rank=i) for i, g in enumerate(gods, 1)]
    for rec, g in zip(nodes, gods):
        rec["degree"] = int(g["degree"])
    return document("god-nodes", graph_path, nodes=nodes, edges=[], truncated=False, shown=len(nodes),
                    total=G.number_of_nodes(), token_budget=None, selection=select("matched"))
