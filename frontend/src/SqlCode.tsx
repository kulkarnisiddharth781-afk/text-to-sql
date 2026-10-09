const KEYWORDS = new Set(
  (
    "SELECT FROM WHERE GROUP BY ORDER HAVING LIMIT OFFSET JOIN INNER LEFT RIGHT FULL OUTER CROSS NATURAL ON USING " +
    "AS AND OR NOT IN IS NULL LIKE ILIKE BETWEEN EXISTS DISTINCT UNION ALL INTERSECT EXCEPT CASE WHEN THEN ELSE END " +
    "ASC DESC WITH OVER PARTITION CAST INTERVAL CREATE INDEX UNIQUE TABLE DROP ALTER INSERT UPDATE DELETE INTO VALUES " +
    "SET TRUE FALSE ANY SOME ROWS RANGE UNBOUNDED PRECEDING FOLLOWING CURRENT ROW NULLS FIRST LAST"
  ).split(" "),
);

// Groups: comment, string, quoted identifier, number, word, anything else.
const TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|("(?:[^"]|"")*"|`[^`]*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)|(\s+|[\s\S])/g;

export default function SqlCode({ code }: { code: string }) {
  const parts: JSX.Element[] = [];
  let key = 0;
  for (const m of code.matchAll(TOKEN)) {
    const [text, comment, str, quoted, num, word] = m;
    let cls = "";
    if (comment) cls = "tok-com";
    else if (str) cls = "tok-str";
    else if (quoted) cls = "tok-id";
    else if (num) cls = "tok-num";
    else if (word) {
      const next = code.slice((m.index ?? 0) + text.length).trimStart()[0];
      if (KEYWORDS.has(word.toUpperCase())) cls = "tok-kw";
      else if (next === "(") cls = "tok-fn";
    }
    parts.push(cls ? <span key={key++} className={cls}>{text}</span> : <span key={key++}>{text}</span>);
  }
  return <code className="sql">{parts}</code>;
}
