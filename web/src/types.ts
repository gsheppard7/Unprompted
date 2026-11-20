export type Proactivity = "off" | "conservative" | "balanced";

export type Health = {
  status: string;
  model_mode: "demo" | "live";
  model_name: string | null;
};

export type Conversation = {
  id: string;
  title: string;
  proactivity_mode: Proactivity;
  created_at: string;
  updated_at: string;
};

export type FeedbackLabel = "useful" | "not_useful" | "too_intrusive";

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  text: string;
  initiator: "user" | "reply" | "proactive";
  reason: string | null;
  created_at: string;
  feedback_label?: FeedbackLabel | null;
};

export type Score = {
  relevance: number;
  evidence: number;
  novelty: number;
  urgency: number;
  interruption_cost: number;
  utility: number;
  threshold: number | null;
};

export type Thought = {
  id: string;
  conversation_id: string;
  trigger_event_id: string | null;
  kind: string;
  draft: string | null;
  score: Score;
  status: string;
  reason: string;
  created_at: string;
};

export type Memory = {
  id: string;
  conversation_id: string;
  kind: string;
  content: string;
  source_message_id: string | null;
  source_label: string | null;
  confidence: number;
  verified: boolean;
  created_at: string;
};

export type ServerEvent = {
  type: string;
  conversation_id: string;
  event_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
};
