"use client";

interface DateDividerProps {
  /** A date string parseable by `new Date()`. */
  date: string;
}

const dayKeyFormatter = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const prettyDateFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: "long",
  year: "numeric",
  month: "long",
  day: "numeric",
});

function startOfDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

function labelFor(date: Date, today: Date): string {
  const dayStart = startOfDay(date);
  const todayStart = startOfDay(today);
  const diffDays = Math.round(
    (todayStart.getTime() - dayStart.getTime()) / (1000 * 60 * 60 * 24)
  );
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return prettyDateFormatter.format(date);
}

export function dayKey(date: string | Date): string {
  return dayKeyFormatter.format(typeof date === "string" ? new Date(date) : date);
}

export function DateDivider({ date }: DateDividerProps) {
  const d = new Date(date);
  return (
    <div className="bg-background sticky top-0 z-10 my-2 flex items-center justify-center">
      <span className="bg-muted text-muted-foreground rounded-full px-3 py-1 text-xs font-medium">
        {labelFor(d, new Date())}
      </span>
    </div>
  );
}
