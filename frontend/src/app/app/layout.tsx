import Link from "next/link";
import { CookieSettingsButton } from "@/components/cookie-settings-button";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <div className="flex-1">{children}</div>
      <footer className="border-t border-slate-200 bg-white px-4 py-3 text-xs text-slate-500">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-3">
          <Link href="/impressum" className="hover:text-slate-700 hover:underline">Impressum</Link>
          <span>·</span>
          <Link href="/datenschutz" className="hover:text-slate-700 hover:underline">Datenschutz</Link>
          <span>·</span>
          <Link href="/agb" className="hover:text-slate-700 hover:underline">AGB</Link>
          <span>·</span>
          <CookieSettingsButton className="hover:text-slate-700 hover:underline" label="Cookie-Einstellungen" />
        </div>
      </footer>
    </div>
  );
}
