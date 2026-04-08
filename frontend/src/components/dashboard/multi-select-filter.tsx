"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { X, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface MultiSelectFilterProps {
  /** Current comma-separated value string */
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  placeholder?: string;
  /** Static options from taxonomy [value, count] */
  options?: [string, number][];
  /** Async search function — if provided, overrides static filtering */
  onSearch?: (q: string) => Promise<{ label: string; count: number }[]>;
  /** "include" = blue pills, "exclude" = red pills */
  variant?: "include" | "exclude";
  /** Extra fixed options prepended to the dropdown */
  extraOptions?: { value: string; label: string }[];
}

const inputCls =
  "w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent";

export function MultiSelectFilter({
  value,
  onChange,
  placeholder = "Add…",
  options = [],
  onSearch,
  variant = "include",
  extraOptions = [],
}: MultiSelectFilterProps) {
  const selected = value ? value.split(",").map((s) => s.trim()).filter(Boolean) : [];

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [asyncResults, setAsyncResults] = useState<{ label: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setAsyncResults([]);
    }
  }, [open]);

  useEffect(() => {
    if (!onSearch || !open) return;
    if (!query.trim()) {
      setAsyncResults([]);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const results = await onSearch(query.trim());
        setAsyncResults(results);
      } finally {
        setLoading(false);
      }
    }, 200);
  }, [query, onSearch, open]);

  const displayOptions: [string, number][] = onSearch
    ? asyncResults.map((r) => [r.label, r.count])
    : query.length > 0
    ? options.filter(([v]) => v.toLowerCase().includes(query.toLowerCase())).slice(0, 50)
    : options.slice(0, 20);

  const add = useCallback(
    (val: string) => {
      if (!val || selected.includes(val)) return;
      const next = [...selected, val];
      onChange(next.join(", "));
      setQuery("");
      setAsyncResults([]);
      inputRef.current?.focus();
    },
    [selected, onChange]
  );

  const remove = useCallback(
    (val: string) => {
      const next = selected.filter((s) => s !== val);
      onChange(next.length > 0 ? next.join(", ") : undefined);
    },
    [selected, onChange]
  );

  const pillCls =
    variant === "exclude"
      ? "bg-red-50 text-red-700 border-red-200 hover:border-red-300"
      : "bg-blue-50 text-blue-700 border-blue-200 hover:border-blue-300";

  return (
    <div ref={containerRef} className="space-y-1.5">
      {/* Selected pills */}
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {selected.map((s) => (
            <span
              key={s}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium",
                pillCls
              )}
            >
              <span className="max-w-[120px] truncate">{s}</span>
              <button
                type="button"
                onClick={() => remove(s)}
                className="opacity-60 hover:opacity-100"
                aria-label={`Remove ${s}`}
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          className={cn(inputCls, "pr-7")}
          placeholder={placeholder}
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setOpen(true);
            setQuery(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
          }}
        />
        <button
          type="button"
          onClick={() => {
            setOpen((o) => !o);
            inputRef.current?.focus();
          }}
          className="absolute right-1.5 top-1.5 text-slate-400 hover:text-slate-600"
        >
          <ChevronDown size={13} className={cn("transition-transform", open && "rotate-180")} />
        </button>
      </div>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 mt-0.5 w-56 max-h-52 overflow-y-auto rounded border border-slate-200 bg-white shadow-md text-sm">
          {loading && (
            <div className="px-2 py-1.5 text-slate-400 italic text-xs">Searching…</div>
          )}
          {!loading && extraOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              disabled={selected.includes(o.value)}
              onClick={() => add(o.value)}
              className={cn(
                "w-full text-left px-2 py-1.5 hover:bg-blue-50 disabled:opacity-40 disabled:cursor-default"
              )}
            >
              {o.label}
            </button>
          ))}
          {!loading && displayOptions.length === 0 && query.length > 0 && (
            <div className="px-2 py-1.5 text-slate-400 italic">No matches</div>
          )}
          {!loading && displayOptions.map(([v, cnt]) => (
            <button
              key={v}
              type="button"
              disabled={selected.includes(v)}
              onClick={() => add(v)}
              className={cn(
                "w-full text-left px-2 py-1.5 hover:bg-blue-50 flex items-center justify-between gap-2 disabled:opacity-40 disabled:cursor-default"
              )}
            >
              <span className="truncate">{v}</span>
              <span className="shrink-0 text-xs text-slate-400">{cnt.toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
