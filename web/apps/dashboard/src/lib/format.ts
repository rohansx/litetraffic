const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Not finished";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateTime.format(date);
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "Not finished";
  const seconds = (new Date(iso).getTime() - now) / 1000;
  if (Number.isNaN(seconds)) return iso;
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function formatMs(value: number | null | undefined): string {
  return value == null ? "n/a" : `${value.toFixed(value < 10 ? 2 : 1)} ms`;
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  return value == null ? "n/a" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatRate(value: number | null | undefined): string {
  return value == null ? "n/a" : `${(value * 100).toFixed(1)}%`;
}

export function formatChange(value: number | null | undefined, unit = "%"): string {
  if (value == null) return "n/a";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}${unit}`;
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/** JSON for display: bare strings unquoted, short values on one line, long ones indented. */
export function formatValue(value: unknown): string {
  if (value === undefined) return "n/a";
  if (typeof value === "string") return value;
  const compact = JSON.stringify(value);
  return compact.length <= 60 ? compact : JSON.stringify(value, null, 2);
}

