"""Structured (--format json) output of the read verbs, and proof the default text output is unchanged (BAI-3692 K3)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "text_golden.json").read_text(encoding="utf-8"))
MINI = FIXTURES / "mini_graph.json"
REL_GRAPH = "graphify-out/graph.json"


@pytest.fixture()
def sandbox(tmp_path):
    """cwd with the mini graph staged at graphify-out/graph.json (the path the goldens were recorded with)."""
    (tmp_path / "graphify-out").mkdir()
    shutil.copy(MINI, tmp_path / REL_GRAPH)
    return tmp_path


def run(cwd, *argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRAPHIFY_")}
    env["PYTHONHASHSEED"] = "0"  # query edge order follows set iteration; the goldens were recorded under seed 0
    return subprocess.run([sys.executable, "-m", "graphify", *argv], cwd=cwd, env=env,
                          capture_output=True, text=True)


def structured(cwd, *argv):
    proc = run(cwd, "--graph", REL_GRAPH, "--format", "json", *argv)
    return proc.returncode, json.loads(proc.stdout), proc


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_text_unchanged_golden(sandbox, name):
    case = GOLDEN[name]
    proc = run(sandbox, *case["argv"], "--graph", REL_GRAPH)
    assert proc.stdout == case["stdout"]
    assert proc.returncode == case["returncode"]


def test_text_unchanged_through_global_flags_and_separator(sandbox):
    """The new grammar in text format renders the very same bytes as the legacy positional form."""
    legacy = GOLDEN["query_over_budget"]
    proc = run(sandbox, "--graph", REL_GRAPH, "query", "--budget", "400", "--", "Hub")
    assert (proc.stdout, proc.returncode) == (legacy["stdout"], legacy["returncode"])
    path = GOLDEN["path_found"]
    proc = run(sandbox, "--format", "text", "--graph", REL_GRAPH, "path", "--", "Alpha", "Derived")
    assert proc.stdout == path["stdout"]


def test_default_argv_is_not_rewritten():
    from graphify import structured as s
    for argv in (["graphify", "query", "x"], ["graphify", "path", "a", "b", "--graph", "g.json"],
                 ["graphify", "-v"], ["graphify", "install"], ["graphify"]):
        assert s.hoist_global_flags(argv) is argv


def test_version_json(sandbox):
    proc = run(sandbox, "--version-json")
    doc = json.loads(proc.stdout)
    assert doc["version"] == "0.9.50"
    assert doc["patch_id"] == "kgs-k3-structured-output-1"


def test_structured_query_matches_text(sandbox):
    rc, doc, _ = structured(sandbox, "query", "--budget", "400", "--", "Hub")
    text = GOLDEN["query_over_budget"]["stdout"]
    text_labels = [ln[5:].rsplit(" [src=", 1)[0] for ln in text.splitlines() if ln.startswith("NODE ")]
    assert rc == 0
    assert [n["label"] for n in doc["nodes"]] == text_labels
    assert len(doc["edges"]) == sum(1 for ln in text.splitlines() if ln.startswith("EDGE "))
    assert doc["truncated"] is False and doc["over_budget"] is True
    assert doc["token_budget"] == 400 and doc["selection"]["candidates"][0]["id"] == "mini_hub"
    ids = {n["id"] for n in doc["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in doc["edges"])


def test_structured_query_truncation(sandbox):
    rc, doc, _ = structured(sandbox, "query", "--budget", "100", "--", "Hub")
    assert doc["truncated"] is True and (doc["shown"], doc["total"]) == (5, 10)
    assert len(doc["nodes"]) == 5 and doc["edges"] == [] and doc["token_budget"] == 100
    assert "showing 5 of 10 nodes" in GOLDEN["query_truncated"]["stdout"]
    _, full, _ = structured(sandbox, "query", "--", "Hub")
    assert full["truncated"] is False and full["token_budget"] == 2000 and full["total"] == 10
    assert [n["id"] for n in doc["nodes"]] == [n["id"] for n in full["nodes"]][:5]


def test_structured_query_no_match(sandbox):
    rc, doc, _ = structured(sandbox, "query", "--", "zzzqq")
    assert rc == 0 and doc["selection"]["status"] == "no_match" and doc["nodes"] == []


def test_structured_explain_direction_and_ids(sandbox):
    rc, doc, _ = structured(sandbox, "explain", "--", "Hub")
    assert rc == 0 and doc["nodes"][0]["id"] == "mini_hub"
    pairs = [(e["source"], e["relation"], e["target"]) for e in doc["edges"]]
    assert ("mini_hub", "references", "mini_derived") in pairs
    assert ("mini_alpha", "calls", "mini_hub") in pairs
    printed = [ln for ln in GOLDEN["explain_hub"]["stdout"].splitlines() if ln.startswith("  <-- ") or ln.startswith("  --> ")]
    assert len(printed) == len(doc["edges"]) == doc["total"]


def test_structured_explain_ambiguous_keeps_both_ids(sandbox):
    rc, doc, _ = structured(sandbox, "explain", "--", "Widget")
    assert rc == 1 and doc["selection"]["status"] == "ambiguous"
    assert [c["id"] for c in doc["selection"]["candidates"]] == ["mini_widget_a", "mini_widget_b"]
    assert [n["id"] for n in doc["nodes"]] == ["mini_widget_a", "mini_widget_b"]


def test_structured_explain_no_match(sandbox):
    rc, doc, _ = structured(sandbox, "explain", "--", "nothing_here_zz")
    assert rc == 0 and doc["selection"]["status"] == "no_match" and doc["nodes"] == []


def test_structured_path_direction(sandbox):
    rc, doc, _ = structured(sandbox, "path", "--", "Alpha", "Derived")
    assert [n["id"] for n in doc["nodes"]] == ["mini_alpha", "mini_hub", "mini_derived"]
    assert [(e["source"], e["relation"], e["target"]) for e in doc["edges"]] == [
        ("mini_alpha", "calls", "mini_hub"), ("mini_hub", "references", "mini_derived")]
    assert "Alpha() --calls [EXTRACTED]--> Hub --references [EXTRACTED]--> Derived" in GOLDEN["path_found"]["stdout"]
    # a hop traversed against its stored direction keeps the stored direction on the edge
    rc, rev, _ = structured(sandbox, "path", "--", "Derived", "Alpha")
    assert rev["selection"]["status"] == "no_match" and rev["selection"]["reason"] == "no_path" and rev["nodes"] == []


def test_structured_affected_hits_in_text_order(sandbox):
    rc, doc, _ = structured(sandbox, "affected", "--", "Hub")
    printed = [ln[2:].split(" [", 1)[0] for ln in GOLDEN["affected_hub"]["stdout"].splitlines() if ln.startswith("- ")]
    hits = [n for n in doc["nodes"] if n["role"] == "hit"]
    assert [h["label"] for h in hits] == printed
    # reverse traversal: the affected node is the edge source, the node it reaches is the target
    assert all(e["target"] in ("mini_hub", "mini_alpha") for e in doc["edges"])
    assert [h["id"] for h in hits if h["label"] == "Widget"] == ["mini_widget_a", "mini_widget_b"]
    widget_b = next(e for e in doc["edges"] if e["source"] == "mini_widget_b")
    assert (widget_b["relation"], widget_b["target"], widget_b["source_location"]) == ("calls", "mini_alpha", "L40")


def test_structured_affected_selection(sandbox):
    rc, doc, _ = structured(sandbox, "affected", "--", "Widget")
    assert doc["selection"]["status"] == "ambiguous"
    assert [c["id"] for c in doc["selection"]["candidates"]] == ["mini_widget_a", "mini_widget_b"]
    rc, doc, _ = structured(sandbox, "affected", "--", "zzqq")
    assert doc["selection"]["status"] == "no_match" and doc["selection"]["candidates"] == []


@pytest.mark.parametrize("verb", ["god-nodes", "god_nodes"])
def test_structured_god_nodes(sandbox, verb):
    rc, doc, _ = structured(sandbox, verb)
    printed = GOLDEN["god_nodes_dash"]["stdout"].splitlines()[1:]
    assert [f"{n['label']} - {n['degree']} edges" for n in doc["nodes"]] == [ln.split(". ", 1)[1] for ln in printed]
    assert [n["id"] for n in doc["nodes"]][:2] == ["mini_hub", "mini_alpha"]
    assert doc["edges"] == []


def test_flag_shaped_value_is_literal(sandbox):
    """`--graph=/other` after `--` is node text; it cannot move the graph path or be parsed as an option."""
    rc, doc, proc = structured(sandbox, "explain", "--", "--graph=/other")
    assert rc == 0 and doc["nodes"][0]["id"] == "mini_flag"
    assert doc["graph_path"] == str((sandbox / REL_GRAPH).resolve())
    rc, doc, _ = structured(sandbox, "query", "--", "--graph=/other")
    assert doc["traversal"]["mode"] == "bfs"
    rc, doc, _ = structured(sandbox, "affected", "--", "--help")
    assert doc["selection"]["status"] in ("no_match", "ambiguous") and "Run 'graphify --help'" not in proc.stdout


def test_values_before_separator_are_rejected_in_literal_form(sandbox):
    proc = run(sandbox, "--graph", REL_GRAPH, "--format", "json", "query", "--graph=/other", "--", "Hub")
    assert proc.returncode == 2 and proc.stdout == ""
    proc = run(sandbox, "--graph", REL_GRAPH, "--format", "json", "path", "--", "OnlyOne")
    assert proc.returncode == 2


def test_global_graph_flag_wins_over_default_not_over_values(sandbox):
    other = sandbox / "other.json"
    shutil.copy(MINI, other)
    proc = run(sandbox, "--graph", "other.json", "--format", "json", "explain", "--", "Hub")
    assert json.loads(proc.stdout)["graph_path"] == str(other.resolve())


def test_structured_read_writes_nothing_beside_the_graph(sandbox):
    before = sorted(p.relative_to(sandbox).as_posix() for p in sandbox.rglob("*"))
    for argv in (["query", "--", "Hub"], ["explain", "--", "Hub"], ["path", "--", "Alpha", "Derived"],
                 ["affected", "--", "Hub"], ["god-nodes"]):
        structured(sandbox, *argv)
    assert sorted(p.relative_to(sandbox).as_posix() for p in sandbox.rglob("*")) == before


def test_bad_format_value(sandbox):
    proc = run(sandbox, "--format", "yaml", "query", "--", "Hub")
    assert proc.returncode == 2
