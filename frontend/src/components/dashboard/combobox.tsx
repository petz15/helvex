"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ComboboxProps {
  options: [string, number][];   // [value, count]
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  placeholder?: string;
  extraOptions?: { value: string; label: string }[];  // prepended fixed options
}

const inputCls =
  "w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent";

export function Combobox({ options, value, onChange, placeholder = "Type to search…", extraOptions = [] }: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync display text when value changes externally
  const selectedLabel =
    extraOptions.find((o) => o.value === value)?.label ??
    (value ? `${value} (${options.find(([v]) => v === value)?.[1] ?? "?"})` : "");

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const filtered = query.length > 0
    ? options.filter(([v]) => v.toLowerCase().includes(query.toLowerCase())).slice(0, 100)
    : options.slice(0, 20);

  const select = useCallback((val: string | undefined) => {
    onChange(val);
    setOpen(false);
    setQuery("");
  }, [onChange]);

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          className={cn(inputCls, "pr-12")}
          placeholder={value ? selectedLabel : placeholder}
          value={open ? query : (value ? selectedLabel : "")}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setOpen(true); setQuery(e.target.value); }}
        />
        <div className="absolute right-1 top-1 flex items-center gap-0.5">
          {value && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); select(undefined); }}
              className="p-0.5 text-slate-400 hover:text-slate-700"
              aria-label="Clear"
            >
              <X size={12} />
            </button>
          )}
          <button
            type="button"
            onClick={() => { setOpen((o) => !o); inputRef.current?.focus(); }}
            className="p-0.5 text-slate-400 hover:text-slate-600"
            aria-label="Toggle"
          >
            <ChevronDown size={13} className={cn("transition-transform", open && "rotate-180")} />
          </button>
        </div>
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-56 overflow-y-auto rounded border border-slate-200 bg-white shadow-md text-sm">
          <button
            type="button"
            onClick={() => select(undefined)}
            className="w-full text-left px-2 py-1.5 text-slate-400 hover:bg-slate-50"
          >
            — All —
          </button>
          {extraOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => select(o.value)}
              className={cn(
                "w-full text-left px-2 py-1.5 hover:bg-blue-50",
                value === o.value && "bg-blue-50 font-medium text-blue-700"
              )}
            >
              {o.label}
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="px-2 py-1.5 text-slate-400 italic">No matches</div>
          )}
          {filtered.map(([v, cnt]) => (
            <button
              key={v}
              type="button"
              onClick={() => select(v)}
              className={cn(
                "w-full text-left px-2 py-1.5 hover:bg-blue-50 flex items-center justify-between gap-2",
                value === v && "bg-blue-50 font-medium text-blue-700"
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
