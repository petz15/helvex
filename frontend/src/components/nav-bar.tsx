"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { cn } from "@/lib/utils";
import { BarChart3, KanbanSquare, Map, Cog, Database, Activity, Building2, Users, UserCircle, Shield, LayoutGrid } from "lucide-react";
import { fetchCurrentUser } from "@/lib/api";

const NAV = [
  { href: "/app/search", label: "Search", icon: BarChart3 },
  { href: "/app/categories", label: "Categories", icon: LayoutGrid },
  { href: "/app/pipeline", label: "Pipeline", icon: KanbanSquare },
  { href: "/app/map", label: "Map", icon: Map },
  { href: "/app/collection", label: "Collection", icon: Database },
  { href: "/app/jobs", label: "Jobs", icon: Activity },
  { href: "/app/org", label: "Team", icon: Users },
  { href: "/app/settings", label: "Settings", icon: Cog },
];

const AUTH_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/accept-invite"];

export function NavBar() {
  const pathname = usePathname();
  const { data: me } = useSWR("me", fetchCurrentUser);

  if (AUTH_PATHS.some((p) => pathname.startsWith(p))) return null;
  return (
    <header className="h-12 bg-white border-b border-slate-200 flex items-center px-4 shrink-0 z-40 shadow-sm">
      <Link href="/app/search" className="flex items-center gap-2 font-bold text-blue-700 mr-6 tracking-tight">
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
      <div className="ml-auto flex items-center gap-1">
        {me?.is_superadmin && (
          <Link
            href="/app/admin"
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors",
              pathname.startsWith("/app/admin")
                ? "bg-purple-600 text-white font-medium"
                : "text-purple-600 hover:bg-purple-50"
            )}
          >
            <Shield size={14} />
            Admin
          </Link>
        )}
        {me?.email && (
          <span className="text-xs text-slate-400 px-2 hidden sm:block truncate max-w-[180px]" title={me.email}>
            {me.email}
          </span>
        )}
        <Link
          href="/app/account"
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors",
            pathname.startsWith("/app/account")
              ? "bg-blue-600 text-white font-medium"
              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          )}
        >
          <UserCircle size={14} />
          Account
        </Link>
        <a href="/logout" className="text-sm text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors">
          Sign out
        </a>
      </div>
    </header>
  );
}
