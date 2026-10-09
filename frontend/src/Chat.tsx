import { useEffect, useRef, useState } from "react";
import { askQuestionStream, Dialect, fetchSchema, HistoryTurn, QueryResponse, TableInfo } from "./api";
import SqlCode from "./SqlCode";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  result?: QueryResponse;
  isError?: boolean;
}

interface Pending {
  status: string;
  sql?: string;
  problem?: string;
}

type Theme = "light" | "dark";

const STORAGE_KEY = "text-to-sql-chat";
const THEME_KEY = "text-to-sql-theme";
const DIALECT_KEY = "text-to-sql-dialect";
const DIALECTS: Record<Dialect, string> = { sqlite: "SQLite", postgres: "PostgreSQL", mysql: "MySQL" };
const DETAILS_KEY = "text-to-sql-details-width";
const DEFAULT_DETAILS = 520;
const MIN_DETAILS = 300;
const SIDEBAR_WIDTH = 240;
const MIN_CHAT = 320;

const clampDetails = (w: number) =>
  Math.min(Math.max(w, MIN_DETAILS), Math.max(MIN_DETAILS, window.innerWidth - SIDEBAR_WIDTH - MIN_CHAT));

function loadDetailsWidth(): number {
  const saved = Number(localStorage.getItem(DETAILS_KEY));
  return saved >= MIN_DETAILS ? clampDetails(saved) : DEFAULT_DETAILS;
}
const MAX_CONTEXT_TURNS = 6;
const EXAMPLES = [
  "Show all customers",
  "Top 5 customers by total spend",
  "Monthly revenue for completed orders in 2024",
  "Best-selling product category by units",
  "Fix this: SELECT nam FROM customers WHERE country = NULL",
  "Show all customers from California", // demonstrates a clarifying question
  "Optimize: SELECT * FROM customers c, orders o WHERE c.id = o.customer_id",
  "Delete all cancelled orders", // demonstrates a polite refusal
];

const newId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

function loadMessages(): Message[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function loadTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function loadDialect(): Dialect {
  const saved = localStorage.getItem(DIALECT_KEY);
  return saved === "postgres" || saved === "mysql" ? saved : "sqlite";
}

function download(filename: string, content: string, mime: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function toCsv(columns: string[], rows: unknown[][]): string {
  const cell = (v: unknown) => {
    if (v === null || v === undefined) return "";
    let s = String(v);
    // Neutralise spreadsheet formula injection in text values.
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
}

function buildHistory(messages: Message[]): HistoryTurn[] {
  const turns: HistoryTurn[] = [];
  messages.forEach((m, i) => {
    const prev = messages[i - 1];
    if (m.role !== "assistant" || prev?.role !== "user") return;
    if (m.result?.status === "ok") turns.push({ question: prev.text, sql: m.result.sql, reply: "" });
    else if (m.result?.status === "clarify") turns.push({ question: prev.text, sql: "", reply: m.result.message });
  });
  return turns.slice(-MAX_CONTEXT_TURNS);
}

function ResultPanel({ result }: { result: QueryResponse }) {
  const [copied, setCopied] = useState(false);

  async function copySql() {
    await navigator.clipboard.writeText(result.sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="panel-stack">
      {result.problem && (
        <section className="card issue">
          <div className="card-head">
            <strong>Issue in your SQL</strong>
          </div>
          <pre className="plain">{result.problem}</pre>
          <div className="card-head why">
            <strong>Why it is incorrect</strong>
          </div>
          <p className="explanation">{result.why_incorrect}</p>
        </section>
      )}

      {result.status === "ok" && result.message && (
        <section className="card issue">
          <p className="explanation">{result.message}</p>
        </section>
      )}

      <section className="card">
        <div className="card-head">
          <strong>Explanation</strong>
        </div>
        <p className="explanation">{result.explanation || "No explanation provided."}</p>
      </section>

      <section className="card">
        <div className="card-head">
          <strong>SQL ({DIALECTS[result.dialect] ?? "SQLite"})</strong>
          <span className="actions">
            <button type="button" className="link" onClick={copySql}>
              {copied ? "Copied" : "Copy SQL"}
            </button>
            <button type="button" className="link" onClick={() => download("query.sql", `${result.sql}\n`, "text/plain")}>
              Download SQL
            </button>
          </span>
        </div>
        <pre>
          <SqlCode code={result.sql} />
        </pre>
        {result.executed_sql &&
          (result.executed_sql.trim() === result.sql.trim() ? (
            <p className="executed sub">Runs unchanged on the SQLite demo database.</p>
          ) : (
            <details className="executed">
              <summary>Run on the SQLite demo database as</summary>
              <pre>
                <SqlCode code={result.executed_sql} />
              </pre>
            </details>
          ))}
      </section>

      {result.optimizations && (
        <section className="card">
          <div className="card-head">
            <strong>Optimizations</strong>
          </div>
          <pre className="plain">{result.optimizations}</pre>
        </section>
      )}

      {result.index_recommendations && (
        <section className="card">
          <div className="card-head">
            <strong>Recommended indexes</strong>
            <button
              type="button"
              className="link"
              onClick={() => navigator.clipboard.writeText(result.index_recommendations)}
            >
              Copy
            </button>
          </div>
          <pre>
            <SqlCode code={result.index_recommendations} />
          </pre>
        </section>
      )}

      {result.columns.length > 0 && (
        <section className="card">
          <div className="card-head">
            <strong>
              {result.rows.length} row{result.rows.length === 1 ? "" : "s"}
            </strong>
            <span className="actions">
              {result.truncated && <span className="sub">Showing first {result.rows.length}</span>}
              <button
                type="button"
                className="link"
                onClick={() => download("results.csv", `\uFEFF${toCsv(result.columns, result.rows)}`, "text/csv;charset=utf-8")}
              >
                Download CSV
              </button>
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {result.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => <td key={j}>{v === null ? "NULL" : String(v)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>(loadMessages);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [schema, setSchema] = useState<TableInfo[]>([]);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [dialect, setDialect] = useState<Dialect>(loadDialect);
  const [detailsWidth, setDetailsWidth] = useState<number>(loadDetailsWidth);
  const endRef = useRef<HTMLDivElement>(null);
  const loading = pending !== null;

  useEffect(() => {
    localStorage.setItem(DIALECT_KEY, dialect);
  }, [dialect]);

  useEffect(() => {
    localStorage.setItem(DETAILS_KEY, String(detailsWidth));
  }, [detailsWidth]);

  useEffect(() => {
    const onResize = () => setDetailsWidth((w) => clampDetails(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  function startResize(e: React.PointerEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = detailsWidth;
    const move = (ev: PointerEvent) => setDetailsWidth(clampDetails(startWidth + startX - ev.clientX));
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    document.body.classList.add("resizing");
  }

  function resizeWithKeys(e: React.KeyboardEvent) {
    if (e.key === "ArrowLeft") setDetailsWidth((w) => clampDetails(w + 32));
    else if (e.key === "ArrowRight") setDetailsWidth((w) => clampDetails(w - 32));
    else return;
    e.preventDefault();
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    fetchSchema().then(setSchema).catch(() => setSchema([]));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // Storage full or unavailable; the chat still works in memory.
    }
  }, [messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  const okMessages = messages.filter((m) => m.result?.status === "ok");
  const selected = messages.find((m) => m.id === selectedId && m.result?.status === "ok") ?? okMessages.at(-1);

  async function submit(q: string = input) {
    const text = q.trim();
    if (!text || loading) return;
    const history = buildHistory(messages);
    setMessages((m) => [...m, { id: newId(), role: "user", text }]);
    setInput("");
    setPending({ status: "Thinking…" });

    const toReply = (r: Partial<Message> & { text: string }): Message => ({ id: newId(), role: "assistant", ...r });
    const out: { reply?: Message } = {};
    try {
      await askQuestionStream(text, history, dialect, (ev) => {
        if (ev.type === "status") {
          setPending((p) => ({ ...p, status: ev.message }));
        } else if (ev.type === "sql") {
          setPending((p) => ({ status: p?.status ?? "", sql: ev.sql, problem: ev.problem }));
        } else if (ev.type === "result") {
          const result: QueryResponse = ev;
          out.reply = toReply({
            result,
            isError: result.status === "error",
            text:
              result.status === "ok"
                ? result.message ||
                  `${result.problem ? "Found an issue in your SQL and corrected it. " : result.optimizations ? "Optimized your SQL. " : ""}Returned ${result.rows.length} row${result.rows.length === 1 ? "" : "s"}.`
                : result.message,
          });
        } else {
          out.reply = toReply({ isError: true, text: ev.message });
        }
      });
    } catch (e) {
      out.reply = toReply({ isError: true, text: e instanceof Error ? e.message : "Something went wrong" });
    }
    const reply = out.reply ?? toReply({ isError: true, text: "The connection closed before an answer arrived." });
    setMessages((m) => [...m, reply]);
    setSelectedId(reply.id);
    setPending(null);
  }

  function newChat() {
    setMessages([]);
    setSelectedId(null);
  }

  return (
    <div className="layout" style={{ "--details-w": `${detailsWidth}px` } as React.CSSProperties}>
      <aside className="sidebar">
        <button type="button" className="primary full" onClick={newChat} disabled={loading || messages.length === 0}>
          New chat
        </button>

        <h2>Schema</h2>
        {schema.map((t) => (
          <details key={t.name}>
            <summary>{t.name}</summary>
            <ul>
              {t.columns.map((c) => (
                <li key={c.name}>
                  {c.name} <span className="type">{c.type}</span>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </aside>

      <main className="chat">
        <header>
          <div>
            <h1>Ask your data</h1>
            <p className="sub">Plain English in, read-only SQL out. Follow-ups refine the previous query.</p>
          </div>
          <div className="header-actions">
            <select
              className="chip"
              value={dialect}
              onChange={(e) => setDialect(e.target.value as Dialect)}
              aria-label="SQL dialect"
              disabled={loading}
            >
              {Object.entries(DIALECTS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="chip"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            >
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </button>
          </div>
        </header>

        <div className="messages" aria-live="polite">
          {messages.length === 0 && (
            <div className="empty">
              <p>Ask a question about your data, then refine it with follow-ups such as "only those from Germany".</p>
              <div className="chips">
                {EXAMPLES.map((ex) => (
                  <button type="button" key={ex} className="chip" onClick={() => submit(ex)}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} id={`msg-${m.id}`} className={`msg ${m.role}`}>
              <div
                className={[
                  "bubble",
                  m.isError ? "error" : "",
                  m.result?.status === "refused" ? "refused" : "",
                  m.result?.status === "clarify" ? "clarify" : "",
                  m.id === selected?.id ? "selected" : "",
                ].join(" ")}
                role={m.isError ? "alert" : undefined}
              >
                <p>{m.text}</p>
                {m.result?.status === "ok" && (
                  <>
                    <button type="button" className="link show-details" onClick={() => setSelectedId(m.id)}>
                      View SQL and results
                    </button>
                    <details className="inline-details" open={m.id === selected?.id}>
                      <summary>SQL and results</summary>
                      <ResultPanel result={m.result} />
                    </details>
                  </>
                )}
              </div>
            </div>
          ))}

          {pending && (
            <div className="msg assistant">
              <div className="bubble" role="status" aria-label="Generating query">
                <span className="dots">
                  <span />
                  <span />
                  <span />
                </span>
                {pending.status && <span className="sub status"> {pending.status}</span>}
                {pending.problem && <p className="explanation">Issue found: {pending.problem}</p>}
                {pending.sql && (
                  <pre>
                    <SqlCode code={pending.sql} />
                  </pre>
                )}
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <textarea
            value={input}
            maxLength={2000}
            rows={2}
            placeholder={messages.length ? "Refine the previous query, paste SQL to fix, or ask something new…" : "e.g. Show all customers, or paste a SQL query to check"}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            aria-label="Your question"
          />
          <button type="submit" className="primary" disabled={loading || !input.trim()}>
            {loading ? "Thinking…" : "Send"}
          </button>
        </form>
      </main>

      <div
        className="resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize details panel"
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={resizeWithKeys}
        onDoubleClick={() => setDetailsWidth(clampDetails(DEFAULT_DETAILS))}
        title="Drag to resize, double-click to reset"
      />

      <aside className="details" aria-label="Query details">
        {selected?.result ? (
          <ResultPanel result={selected.result} />
        ) : (
          <p className="sub">The SQL, explanation and results for the selected answer appear here.</p>
        )}
      </aside>
    </div>
  );
}
