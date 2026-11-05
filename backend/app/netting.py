"""PRD section 9 — debt netting over a directed graph (deterministic, auditable)."""
from __future__ import annotations
from collections import defaultdict


def find_cycle(graph: dict[str, dict[str, float]]) -> list[str] | None:
    """Return one directed cycle as a node list (last == first), or None."""
    visited: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visited[node] = 1
        stack.append(node)
        for nxt, amt in graph.get(node, {}).items():
            if amt <= 0:
                continue
            if nxt not in visited:
                hit = dfs(nxt)
                if hit:
                    return hit
            elif visited[nxt] == 1:
                i = stack.index(nxt)
                return stack[i:] + [nxt]
        stack.pop()
        visited[node] = 2
        return None

    for node in list(graph):
        if node not in visited:
            hit = dfs(node)
            if hit:
                return hit
    return None


def propose_netting(debts: list[dict]) -> dict:
    """Cancel the minimum edge amount along each cycle until acyclic."""
    graph: dict[str, dict[str, float]] = defaultdict(dict)
    for d in debts:
        graph[d["debtor"]][d["creditor"]] = graph[d["debtor"]].get(d["creditor"], 0) + float(d["amount"])
        graph.setdefault(d["creditor"], {})
    original = sum(v for outs in graph.values() for v in outs.values())
    cancellations: list[dict] = []
    while True:
        cycle = find_cycle(graph)
        if not cycle:
            break
        edges = list(zip(cycle[:-1], cycle[1:]))
        m = min(graph[a][b] for a, b in edges)
        for a, b in edges:
            graph[a][b] -= m
            if graph[a][b] <= 1e-9:
                del graph[a][b]
        cancellations.append({"cycle": cycle, "cancelled_amount": m})
    remaining = [{"debtor": a, "creditor": b, "amount": round(v, 2)} for a, outs in graph.items() for b, v in outs.items() if v > 1e-9]
    netted = sum(r["amount"] for r in remaining)
    lines = [f"Found {len(cancellations)} circular debt ring(s)."]
    for c in cancellations:
        ring = " → ".join(c["cycle"])
        lines.append(f"Cancel {c['cancelled_amount']:g} along {ring}: no cash needs to move.")
    if not remaining:
        lines.append("All debts net to zero — zero cash movement required.")
    else:
        lines.append(f"{len(remaining)} obligation(s) remain, totalling {netted:g}.")
    lines.append("Each party must still confirm before anything is recorded.")
    return {
        "original_cash_movement": original,
        "netted_cash_movement": netted,
        "cancellations": cancellations,
        "remaining": remaining,
        "explanation": " ".join(lines),
    }
