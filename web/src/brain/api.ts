import type {
  BrainMemory,
  BrainProfile,
  GraphData,
  Overview,
  QuestionState,
  TeachResult,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* The body was not JSON. */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function getBrain(): Promise<Overview> {
  return request("/brain");
}

export function updateBrain(patch: { name?: string; questioning_paused?: boolean }): Promise<BrainProfile> {
  return request("/brain", { method: "PATCH", body: JSON.stringify(patch) });
}

export function refreshSummary(): Promise<Overview> {
  return request("/brain/summary", { method: "POST", body: "{}" });
}

export function exportBrain(): Promise<Record<string, unknown>> {
  return request("/brain/export");
}

export function clearBrain(): Promise<{ ok: boolean }> {
  return request("/brain?confirm=true", { method: "DELETE" });
}

export function teachBrain(text: string, origin: "manual" | "conversation" = "manual"): Promise<TeachResult> {
  return request("/brain/teach", { method: "POST", body: JSON.stringify({ text, origin }) });
}

export function uploadBrain(file: File): Promise<TeachResult> {
  const body = new FormData();
  body.append("file", file);
  return request("/brain/upload", { method: "POST", body });
}

export function listBrainMemories(params: { status?: string; category?: string; q?: string }): Promise<{ memories: BrainMemory[] }> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.category) search.set("category", params.category);
  if (params.q) search.set("q", params.q);
  const query = search.toString();
  return request(`/brain/memories${query ? `?${query}` : ""}`);
}

export function updateBrainMemory(
  id: string,
  patch: Partial<Pick<BrainMemory, "content" | "category" | "stance" | "status" | "valid_from" | "valid_to" | "supersedes_id">>,
): Promise<BrainMemory> {
  return request(`/brain/memories/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteBrainMemory(id: string): Promise<{ ok: boolean }> {
  return request(`/brain/memories/${id}`, { method: "DELETE" });
}

export function reviewMemories(acceptIds: string[], rejectIds: string[]): Promise<{ accepted: BrainMemory[]; rejected: BrainMemory[] }> {
  return request("/brain/memories/review", {
    method: "POST",
    body: JSON.stringify({ accept_ids: acceptIds, reject_ids: rejectIds }),
  });
}

export function getGraph(): Promise<GraphData> {
  return request("/brain/graph");
}

export function getQuestions(): Promise<QuestionState> {
  return request("/brain/questions");
}

export function generateQuestions(refresh: boolean): Promise<QuestionState> {
  return request("/brain/questions/generate", { method: "POST", body: JSON.stringify({ refresh }) });
}

export function answerQuestion(id: string, text: string): Promise<TeachResult> {
  return request(`/brain/questions/${id}/answer`, { method: "POST", body: JSON.stringify({ text }) });
}

export function skipQuestion(id: string): Promise<QuestionState> {
  return request(`/brain/questions/${id}/skip`, { method: "POST", body: "{}" });
}

export function dismissQuestion(id: string): Promise<QuestionState> {
  return request(`/brain/questions/${id}/dismiss`, { method: "POST", body: "{}" });
}

export function brainSocket(): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${protocol}://${window.location.host}/ws/brain`);
}
