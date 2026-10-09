export type Dialect = "sqlite" | "postgres" | "mysql";

export interface QueryResponse {
  status: "ok" | "refused" | "error" | "clarify";
  message: string;
  sql: string;
  dialect: Dialect;
  executed_sql: string;
  explanation: string;
  problem: string;
  why_incorrect: string;
  optimizations: string;
  index_recommendations: string;
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  attempts: number;
}

export interface TableInfo {
  name: string;
  columns: { name: string; type: string; pk: boolean }[];
}

export interface HistoryTurn {
  question: string;
  sql: string;
  reply: string;
}

export async function askQuestion(question: string, history: HistoryTurn[] = []): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  return res.json();
}

export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "sql"; sql: string; problem: string; why_incorrect: string; optimizations: string; index_recommendations: string }
  | ({ type: "result" } & QueryResponse)
  | { type: "failure"; message: string };

export async function askQuestionStream(
  question: string,
  history: HistoryTurn[],
  dialect: Dialect,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const res = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history, dialect }),
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

export async function fetchSchema(): Promise<TableInfo[]> {
  const res = await fetch("/api/schema");
  if (!res.ok) throw new Error("Could not load schema");
  return res.json();
}
