// Types mirror copilot/models.py and the JSON returned by copilot/api.py.

export type Severity = "info" | "warning" | "critical";
export type Status = "queued" | "running" | "completed" | "failed";
export type Reasoner = "claude" | "heuristic";
export type Verdict = "correct" | "partially_correct" | "incorrect";

export const CATEGORIES = [
  "bad_deploy", "config_change", "memory_leak", "crash_loop", "cpu_saturation", "network_latency",
  "dependency_unavailable", "datastore_degradation", "resource_exhaustion", "queue_backlog", "probe_failure", "unknown",
] as const;
export type Category = (typeof CATEGORIES)[number];

export interface Alert {
  name: string;
  service: string;
  severity: Severity;
  summary: string;
  labels: Record<string, string>;
  starts_at: string;
}

export interface Evidence {
  id: string;
  source: "metrics" | "traces" | "logs" | "k8s_events" | "changes" | "knowledge";
  service: string | null;
  title: string;
  detail: string;
  severity: Severity;
  link: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface Finding {
  kind: string;
  service: string;
  score: number;
  summary: string;
  evidence_ids: string[];
}

export interface SuggestedAction {
  title: string;
  command: string | null;
  risk: "low" | "medium" | "high";
  rationale: string;
}

export interface Hypothesis {
  root_cause_service: string;
  category: Category;
  title: string;
  summary: string;
  reasoning: string;
  confidence: number;
  supporting_evidence: { evidence_id: string; why_it_matters: string }[];
  suggested_actions: SuggestedAction[];
  related_documents: string[];
  alternatives: { root_cause_service: string; category: Category; why_less_likely: string }[];
  reasoner: string;
}

export interface Grading {
  correct: boolean;
  service_correct: boolean;
  category_correct: boolean;
  expected_service: string;
  expected_category: string;
}

export interface Feedback {
  verdict: Verdict;
  actual_root_cause_service: string | null;
  actual_category: Category | null;
  notes: string;
  created_at: string;
}

export interface Investigation {
  id: string;
  alert: Alert;
  status: Status;
  scenario_id: string | null;
  reasoner: string;
  telemetry_mode: string;
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  hypothesis: Hypothesis | null;
  evidence: Evidence[];
  findings: Finding[];
  retrieved_documents: KnowledgeDoc[];
  services_examined: string[];
  error: string | null;
  notes: string[];
  grading: Grading | null;
  feedback: Feedback | null;
}

export interface InvestigationSummary {
  id: string;
  status: Status;
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  alert: Alert;
  scenario_id: string | null;
  reasoner: string;
  telemetry_mode: string;
  hypothesis: { root_cause_service: string; category: Category; title: string; confidence: number } | null;
  feedback: Verdict | null;
  grading: Grading | null;
  error: string | null;
}

export interface StepEvent {
  id: number;
  investigation_id: string;
  ts: string;
  node: string;
  status: "started" | "progress" | "completed" | "failed";
  message: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface Scenario {
  id: string;
  title: string;
  description: string;
  difficulty: "easy" | "medium" | "hard";
  alert: Alert;
  tags: string[];
  reproduce_method: string;
}

export interface KnowledgeDoc {
  id: string;
  title: string;
  type: "postmortem" | "runbook" | "incident";
  services: string[];
  category: string | null;
  date: string | null;
  snippet: string;
  body?: string;
  score?: number;
}

export interface EvalResult {
  scenario_id: string;
  title: string;
  difficulty: "easy" | "medium" | "hard";
  investigation_id: string;
  status: Status;
  duration_ms: number | null;
  grading: (Grading & { predicted_service?: string; predicted_category?: string; confidence?: number }) | null;
  predicted: string | null;
  expected: string;
  reasoner_used: string;
}

export interface EvalSummary {
  n: number;
  completed: number;
  accuracy: number;
  service_accuracy: number;
  category_accuracy: number;
  service_recall_at_3: number;
  median_time_to_hypothesis_ms: number | null;
  by_difficulty: Record<string, { n: number; accuracy: number }>;
  by_category: Record<string, { n: number; accuracy: number }>;
}

export interface EvalRun {
  id: string;
  created_at: string;
  finished_at?: string;
  status: "running" | "completed";
  reasoner: Reasoner;
  model: string | null;
  scenario_ids: string[];
  results?: EvalResult[];
  summary: Partial<EvalSummary>;
}

export interface Stats {
  total: number;
  completed: number;
  failed: number;
  running: number;
  median_time_to_hypothesis_ms: number | null;
  feedback: { count: number; verdicts: Record<string, number>; accuracy: number | null };
  scenario_accuracy: number | null;
  scenario_runs: number;
  by_category: Record<string, number>;
  by_reasoner: Record<string, number>;
  knowledge_documents: number;
  learned_incidents: number;
}

export interface RuntimeConfig {
  telemetry_mode: string;
  reasoner: Reasoner;
  llm_enabled: boolean;
  model: string | null;
  slack_enabled: boolean;
  namespace: string;
  scenarios: number;
  knowledge_documents: number;
}

export interface Topology {
  nodes: { id: string; kind: "service" | "datastore" }[];
  edges: { source: string; target: string }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  config: () => request<RuntimeConfig>("GET", "/api/config"),
  stats: () => request<Stats>("GET", "/api/stats"),
  topology: () => request<Topology>("GET", "/api/topology"),
  investigations: (limit = 100) =>
    request<{ items: InvestigationSummary[]; total: number }>("GET", `/api/investigations?limit=${limit}`),
  investigation: (id: string) => request<Investigation>("GET", `/api/investigations/${id}`),
  events: (id: string) => request<StepEvent[]>("GET", `/api/investigations/${id}/events`),
  startScenario: (scenario_id: string, reasoner: Reasoner) =>
    request<InvestigationSummary>("POST", "/api/investigations", { scenario_id, reasoner }),
  feedback: (id: string, body: { verdict: Verdict; actual_root_cause_service?: string; actual_category?: Category; notes?: string }) =>
    request<Feedback>("POST", `/api/investigations/${id}/feedback`, body),
  scenarios: () => request<Scenario[]>("GET", "/api/scenarios"),
  knowledge: () => request<KnowledgeDoc[]>("GET", "/api/knowledge"),
  searchKnowledge: (q: string) => request<KnowledgeDoc[]>("GET", `/api/knowledge/search?q=${encodeURIComponent(q)}&k=8`),
  document: (id: string) => request<KnowledgeDoc>("GET", `/api/knowledge/${encodeURIComponent(id)}`),
  evals: () => request<EvalRun[]>("GET", "/api/evals"),
  evalRun: (id: string) => request<EvalRun>("GET", `/api/evals/${id}`),
  startEval: (reasoner: Reasoner, scenario_ids?: string[]) =>
    request<{ id: string }>("POST", "/api/evals", { reasoner, scenario_ids }),
};

/** Subscribe to the agent's live steps (Server-Sent Events). Returns an unsubscribe function. */
export function streamSteps(id: string, onStep: (e: StepEvent) => void, onDone: () => void): () => void {
  const es = new EventSource(`/api/investigations/${id}/stream`);
  es.addEventListener("step", (m) => onStep(JSON.parse((m as MessageEvent).data) as StepEvent));
  es.addEventListener("done", () => {
    es.close();
    onDone();
  });
  es.onerror = () => {
    // the server closes the stream after `done`; any other error: stop and let the page fall back to polling
    es.close();
    onDone();
  };
  return () => es.close();
}
