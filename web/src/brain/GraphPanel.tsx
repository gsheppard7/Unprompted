import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Node,
  type OnNodesChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getGraph } from "./api";
import { categoryLabel, stanceLabel, type GraphData } from "./types";

type Props = {
  revision: number;
};

export function GraphPanel({ revision }: Props) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [relationship, setRelationship] = useState("all");
  const [category, setCategory] = useState("all");
  const [stance, setStance] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getGraph()
      .then((graph) => {
        if (!cancelled) {
          setData(graph);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load the graph.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const relationships = useMemo(() => {
    const labels = new Set((data?.edges ?? []).map((edge) => edge.label));
    return ["all", ...labels];
  }, [data]);

  const categories = useMemo(() => {
    const labels = new Set((data?.edges ?? []).flatMap((edge) => edge.categories));
    return ["all", ...labels];
  }, [data]);

  const visibleEdges = useMemo(() => {
    return (data?.edges ?? []).filter((edge) => {
      if (relationship !== "all" && edge.label !== relationship) return false;
      if (stance !== "all" && edge.stance !== stance) return false;
      if (category !== "all" && !edge.categories.includes(category)) return false;
      return true;
    });
  }, [data, relationship, stance, category]);

  const visibleNodes = useMemo(() => {
    const ids = new Set(visibleEdges.flatMap((edge) => [edge.source, edge.target]));
    return (data?.nodes ?? []).filter((node) => ids.has(node.id));
  }, [data, visibleEdges]);

  useEffect(() => {
    const self = visibleNodes.find((node) => node.is_self);
    const others = visibleNodes.filter((node) => node !== self);
    const radius = Math.max(190, others.length * 42);
    setNodes(
      visibleNodes.map((node) => {
        const index = others.indexOf(node);
        const angle = (Math.PI * 2 * index) / Math.max(others.length, 1) - Math.PI / 2;
        const position = node.is_self || index < 0
          ? { x: 0, y: 0 }
          : { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
        return {
          id: node.id,
          position,
          data: { label: node.label },
          style: node.is_self
            ? { background: "#0f6b60", color: "#f7f1e7", border: "none", borderRadius: 999, padding: "8px 12px" }
            : { background: "#fff", border: "1px solid #e0d5c6", borderRadius: 12, color: "#1c1916" },
        };
      }),
    );
    setSelected(null);
  }, [visibleNodes]);

  const onNodesChange: OnNodesChange = useCallback((changes) => {
    setNodes((current) => changes.applyTo(current));
  }, []);

  const edges = visibleEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    style: edge.stance === "explicit" ? { stroke: "#0f6b60" } : { stroke: "#8a5a16", strokeDasharray: "6 4" },
    labelStyle: { fill: "#1c1916", fontSize: 11 },
  }));

  const selectedNode = visibleNodes.find((node) => node.id === selected) ?? null;
  const related = selectedNode
    ? (data?.memories ?? []).filter((memory) => selectedNode.memory_ids.includes(memory.id))
    : [];

  return (
    <div className="brain-stack">
      <form className="filter-bar">
        <label>
          Relationship
          <select value={relationship} onChange={(event) => setRelationship(event.target.value)}>
            {relationships.map((item) => (
              <option key={item} value={item}>
                {item === "all" ? "All" : item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            {categories.map((item) => (
              <option key={item} value={item}>
                {item === "all" ? "All" : categoryLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Stance
          <select value={stance} onChange={(event) => setStance(event.target.value)}>
            <option value="all">All</option>
            <option value="explicit">Stated</option>
            <option value="inferred">Inferred</option>
          </select>
        </label>
      </form>
      <p className="muted">Solid lines were stated. Dashed lines were inferred. Only accepted relationships are drawn.</p>
      {error && <p className="banner">{error}</p>}
      {loading ? (
        <p className="muted">Loading the graph…</p>
      ) : visibleNodes.length === 0 ? (
        <p className="muted">Accept a few structured memories and the people, interests, and projects will appear here.</p>
      ) : (
        <div className="brain-graph">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onNodeClick={(_event, node) => setSelected(node.id)}
            fitView
            nodesConnectable={false}
            edgesReconnectable={false}
            deleteKeyCode={null}
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      )}
      {selectedNode && (
        <section className="summary-card">
          <h2>{selectedNode.label}</h2>
          <p className="muted">{categoryLabel(selectedNode.entity_type)}</p>
          {related.length === 0 ? (
            <p className="muted">No memory is attached to this node.</p>
          ) : (
            <ul className="plain-list">
              {related.map((memory) => (
                <li key={memory.id}>
                  <strong>{memory.content}</strong>
                  <span>
                    {memory.category ? categoryLabel(memory.category) : "Memory"} · {stanceLabel(memory.stance ?? "")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {visibleEdges.length > 0 && (
        <ul className="edge-list">
          {visibleEdges.map((edge) => {
            const source = data?.nodes.find((node) => node.id === edge.source)?.label ?? "Something";
            const target = data?.nodes.find((node) => node.id === edge.target)?.label ?? "something";
            return (
              <li key={edge.id}>
                {source} → {edge.label} → {target}
                <span>{edge.stance === "explicit" ? "stated" : "inferred"}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
