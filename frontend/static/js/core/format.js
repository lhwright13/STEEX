// core/format.js — value formatters (replaces fmtMoney/money/formatTime/
// formatDuration duplicated across the legacy scripts, P-5).
export function money(v) {
  if (v == null || isNaN(v)) return "—";
  const n = Number(v);
  return (n < 0 ? "-$" : "$") + Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

export function pct(v, digits = 2) {
  if (v == null || isNaN(v)) return "—";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(digits) + "%";
}

// "3:35 PM" style clock from an ISO timestamp (times are UTC per the footer).
export function timeOfDay(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString("en-US", {
    hour: "numeric", minute: "2-digit", timeZone: "UTC",
  }) + " UTC";
}

// "12m ago" / "3h ago" relative label.
export function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}
