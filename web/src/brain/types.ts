export const CATEGORIES = [
  "identity",
  "education",
  "interests",
  "projects",
  "experiences",
  "goals",
  "preferences",
  "skills",
  "relationships",
  "other",
] as const;

export type BrainCategory = (typeof CATEGORIES)[number];
export type MemoryStatus = "proposed" | "accepted" | "rejected" | "superseded";
export type Stance = "explicit" | "inferred" | "hypothesis";
export type BrainTab = "overview" | "teach" | "upload" | "memories" | "graph" | "questions";

export type BrainSourceRef = {
  id: string | null;
  source_type: string | null;
  filename: string | null;
  label: string;
  created_at: string | null;
  preview: string;
};

export type BrainMemory = {
  id: string;
  brain_id: string;
  content: string;
  category: BrainCategory;
  status: MemoryStatus;
  stance: Stance;
  confidence: number;
  confidence_note: string;
  source_id: string | null;
  evidence_text: string | null;
  valid_from: string | null;
  valid_to: string | null;
  supersedes_id: string | null;
  created_at: string;
  updated_at: string;
  entities: { name: string; entity_type: string }[];
  relationships: { source: string; target: string; relationship_type: string; stance: string }[];
  source: BrainSourceRef;
};

export type Duplicate = {
  content: string;
  matched_id: string;
  matched_status: string;
  matched_content: string;
};

export type TeachResult = {
  source_id: string;
  extractor: "model" | "fallback";
  notice: string;
  proposed: BrainMemory[];
  duplicates: Duplicate[];
};

export type BrainProfile = {
  id: string;
  name: string;
  questioning_paused: boolean;
  summary: string | null;
  summary_mode: string | null;
  summary_updated_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Overview = {
  profile: BrainProfile;
  summary: {
    text: string;
    sections: Record<string, string[]>;
    uncertainties: string[];
    accepted_count: number;
    basis: string;
  };
  narrative: { text: string; notice: string } | null;
  counts: {
    accepted: number;
    proposed: number;
    relationships: number;
    pending_questions: number;
    entities: number;
  };
  recent_memories: BrainMemory[];
  recent_changes: { id: string; kind: string; detail: string; created_at: string }[];
  extractor: "model" | "fallback";
  confidence_note: string;
  notices: string[];
  summary_notice?: string;
};

export type BrainQuestion = {
  id: string;
  question_text: string;
  status: string;
  motivation: string | null;
  usefulness: number;
  generator: string;
  created_at: string;
};

export type QuestionState = {
  paused: boolean;
  questions: BrainQuestion[];
  message?: string | null;
  generator?: string;
  notice?: string;
  refreshed?: boolean;
};

export type GraphNode = {
  id: string;
  label: string;
  entity_type: string;
  is_self: boolean;
  memory_ids: string[];
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  stance: string;
  status: string;
  memory_ids: string[];
  categories: string[];
};

export type GraphMemory = {
  id: string;
  content: string | null;
  category: string | null;
  stance: string | null;
  status: string | null;
};

export type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  memories: GraphMemory[];
};

export function categoryLabel(category: string): string {
  return category.charAt(0).toUpperCase() + category.slice(1);
}

export function stanceLabel(stance: string): string {
  if (stance === "explicit") return "Stated";
  if (stance === "inferred") return "Inferred";
  if (stance === "hypothesis") return "Hypothesis";
  return stance;
}

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const SELF_KNOWLEDGE =
  /\b(i'm a|i am a|i'm an|i am an|i study|i'm studying|i am studying|i enjoy|i like|i love|i work on|i'm working|i am working|my goal|i want to|i hope to|i prefer|i'm interested|i am interested|interested in|i'm skilled|i am skilled|i'm good at|i am good at)\b/i;

export function looksLikeSelfKnowledge(text: string): boolean {
  return text.length <= 2000 && SELF_KNOWLEDGE.test(text);
}
