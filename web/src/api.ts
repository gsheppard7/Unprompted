import type {
  Conversation,
  FeedbackLabel,
  Health,
  Memory,
  Message,
  Proactivity,
  Thought,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return request("/health");
}

export function listConversations(): Promise<Conversation[]> {
  return request("/conversations");
}

export function createConversation(): Promise<Conversation> {
  return request("/conversations", { method: "POST", body: JSON.stringify({}) });
}

export function updateConversation(
  id: string,
  patch: { title?: string; proactivity_mode?: Proactivity },
): Promise<Conversation> {
  return request(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteConversation(id: string): Promise<{ ok: boolean }> {
  return request(`/conversations/${id}`, { method: "DELETE" });
}

export function listMessages(id: string): Promise<Message[]> {
  return request(`/conversations/${id}/messages`);
}

export function listThoughts(id: string): Promise<Thought[]> {
  return request(`/conversations/${id}/thoughts`);
}

export function listMemories(id: string): Promise<Memory[]> {
  return request(`/conversations/${id}/memories`);
}

export function createMemory(
  id: string,
  body: { content: string; source_label: string; verified: boolean },
): Promise<Memory> {
  return request(`/conversations/${id}/memories`, {
    method: "POST",
    body: JSON.stringify({ ...body, kind: "fact" }),
  });
}

export function deleteMemory(id: string): Promise<{ ok: boolean }> {
  return request(`/memories/${id}`, { method: "DELETE" });
}

export function sendFeedback(messageId: string, label: FeedbackLabel): Promise<Message> {
  return request("/feedback", {
    method: "POST",
    body: JSON.stringify({ message_id: messageId, label }),
  });
}

export function conversationSocket(id: string): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${protocol}://${window.location.host}/ws/conversations/${id}`);
}
