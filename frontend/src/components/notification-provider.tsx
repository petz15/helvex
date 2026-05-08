"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

export type NotificationKind = "error" | "warning" | "info" | "success";

export interface Notification {
  id: string;
  kind: NotificationKind;
  title: string;
  message?: string;
  /** Optional action rendered as a link/button below the message. */
  action?: { label: string; href?: string; onClick?: () => void };
  /** Auto-dismiss after this many ms. 0 = never. Default: 6000. */
  duration?: number;
}

interface NotificationContextValue {
  notify: (n: Omit<Notification, "id">) => void;
  dismiss: (id: string) => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

export function useNotifications(): NotificationContextValue {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error("useNotifications must be used inside NotificationProvider");
  return ctx;
}

let _counter = 0;

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Notification[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setItems((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const notify = useCallback(
    (n: Omit<Notification, "id">) => {
      const id = `n-${++_counter}`;
      const duration = n.duration ?? 6000;
      setItems((prev) => [...prev.slice(-4), { ...n, id }]); // cap at 5 visible
      if (duration > 0) {
        const t = setTimeout(() => dismiss(id), duration);
        timers.current.set(id, t);
      }
    },
    [dismiss],
  );

  // Clean up timers on unmount
  useEffect(() => {
    const t = timers.current;
    return () => t.forEach((timer) => clearTimeout(timer));
  }, []);

  return (
    <NotificationContext.Provider value={{ notify, dismiss }}>
      {children}
      <NotificationToaster items={items} onDismiss={dismiss} />
    </NotificationContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Toaster UI
// ---------------------------------------------------------------------------

const KIND_STYLES: Record<NotificationKind, { bar: string; icon: string; bg: string }> = {
  error:   { bar: "bg-red-500",    icon: "✕",  bg: "bg-red-50 border-red-200" },
  warning: { bar: "bg-amber-400",  icon: "⚠",  bg: "bg-amber-50 border-amber-200" },
  info:    { bar: "bg-blue-500",   icon: "ℹ",  bg: "bg-blue-50 border-blue-200" },
  success: { bar: "bg-emerald-500",icon: "✓",  bg: "bg-emerald-50 border-emerald-200" },
};

function NotificationToaster({
  items,
  onDismiss,
}: {
  items: Notification[];
  onDismiss: (id: string) => void;
}) {
  if (items.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]"
    >
      {items.map((n) => {
        const s = KIND_STYLES[n.kind];
        return (
          <div
            key={n.id}
            role="alert"
            className={`flex overflow-hidden rounded-lg border shadow-lg animate-in slide-in-from-right-4 ${s.bg}`}
          >
            <div className={`w-1 shrink-0 ${s.bar}`} />
            <div className="flex flex-1 gap-3 p-3">
              <span className="mt-0.5 text-sm leading-none">{s.icon}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-slate-800 leading-snug">{n.title}</p>
                {n.message && (
                  <p className="mt-0.5 text-xs text-slate-600 leading-snug">{n.message}</p>
                )}
                {n.action && (
                  <div className="mt-1.5">
                    {n.action.href ? (
                      <a
                        href={n.action.href}
                        className="text-xs font-medium text-blue-600 hover:underline"
                      >
                        {n.action.label} →
                      </a>
                    ) : (
                      <button
                        onClick={n.action.onClick}
                        className="text-xs font-medium text-blue-600 hover:underline"
                      >
                        {n.action.label} →
                      </button>
                    )}
                  </div>
                )}
              </div>
              <button
                onClick={() => onDismiss(n.id)}
                aria-label="Dismiss"
                className="ml-1 shrink-0 text-slate-400 hover:text-slate-600 text-xs leading-none mt-0.5"
              >
                ✕
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
