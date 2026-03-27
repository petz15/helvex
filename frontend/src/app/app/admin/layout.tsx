"use client";
import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { Shield, Users, Building2 } from "lucide-react";
import { fetchCurrentUser } from "@/lib/api";
import { cn } from "@/lib/utils";

const ADMIN_NAV = [
  { href: "/app/admin/users", label: "Users", icon: Users },
  { href: "/app/admin/orgs", label: "Organizations", icon: Building2 },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { data: me, isLoading } = useSWR("me", fetchCurrentUser);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && me && !me.is_superadmin) {
      router.replace("/app/search");
    }
  }, [me, isLoading, router]);

  if (isLoading || !me) return null;
  if (!me.is_superadmin) return null;

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-44 shrink-0 border-r border-slate-200 bg-slate-50 flex flex-col p-2 gap-0.5">
        <div className="flex items-center gap-2 px-3 py-2 mb-1">
          <Shield size={13} className="text-purple-600" />
          <span className="text-xs font-semibold text-purple-700 uppercase tracking-wider">Superadmin</span>
        </div>
        {ADMIN_NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors",
              pathname.startsWith(href)
                ? "bg-purple-100 text-purple-800 font-medium"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            )}
          >
            <Icon size={14} />
            {label}
          </Link>
        ))}
      </aside>
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
