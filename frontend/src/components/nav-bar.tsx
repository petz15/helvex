"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { BarChart3, KanbanSquare, Map, Cog, Database, Activity, Building2 } from "lucide-react";

const NAV = [
  { href: "/app/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/app/pipeline", label: "Pipeline", icon: KanbanSquare },
  { href: "/app/map", label: "Map", icon: Map },
  { href: "/app/collection", label: "Collection", icon: Database },
  { href: "/app/jobs", label: "Jobs", icon: Activity },
  { href: "/app/settings", label: "Settings", icon: Cog },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <header className="h-12 bg-white border-b border-slate-200 flex items-center px-4 shrink-0 z-40 shadow-sm">
      <Link href="/app/dashboard" className="flex items-center gap-2 font-bold text-blue-700 mr-6 tracking-tight">
        <Building2 size={18} />
        Helvex
      </Link>
      <nav className="flex items-center gap-0.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors",
                active
                  ? "bg-blue-600 text-white font-medium"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              )}
            >
              <Icon size={14} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto">
        <a href="/logout" className="text-sm text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors">
          Sign out
        </a>
      </div>
    </header>
  );
}
