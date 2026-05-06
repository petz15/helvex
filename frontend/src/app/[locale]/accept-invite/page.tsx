"use client";
import { useEffect, useState, FormEvent, Suspense } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { Building2, Loader2, AlertTriangle, Check } from "lucide-react";
import { fetchCurrentUser, fetchInvitePreview, acceptInvite, registerAndAcceptInvite, type InvitePreview } from "@/lib/api";
import { useI18n } from "@/i18n/context";

function sub(template: string, vars: Record<string, string>): string {
  return Object.entries(vars).reduce((s, [k, v]) => s.replace(new RegExp(`\\{${k}\\}`, "g"), v), template);
}

function AcceptInviteContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const locale = pathname?.split("/")[1] ?? "de";
  const token = searchParams?.get("token") ?? "";
  const { dict } = useI18n();
  const t = dict.auth.acceptInvite;

  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [done, setDone] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  const [currentOrgName, setCurrentOrgName] = useState<string | null>(null);
  const [showSwitchWarning, setShowSwitchWarning] = useState(false);

  const [password, setPassword] = useState("");
  const [registering, setRegistering] = useState(false);

  useEffect(() => {
    if (!token) {
      setError(t.noToken);
      setLoading(false);
      return;
    }

    async function load() {
      try {
        const [inv, me] = await Promise.all([
          fetchInvitePreview(token),
          fetchCurrentUser().catch(() => null),
        ]);
        setPreview(inv);
        setIsLoggedIn(!!me);
        if (me?.org && me.org.id !== inv.org_id) {
          setCurrentOrgName(me.org.name);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Invalid or expired invite link.");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [token]);

  async function handleAccept(force = false) {
    setAccepting(true);
    setError(null);
    try {
      await acceptInvite(token, force);
      setDone(true);
      setTimeout(() => router.push(`/${locale}/app/search`), 1500);
    } catch (e: unknown) {
      const err = e as { message?: string; detail?: { code?: string; current_org_name?: string } };
      if (err?.detail?.code === "already_in_org") {
        setCurrentOrgName(err.detail.current_org_name ?? "your current org");
        setShowSwitchWarning(true);
      } else {
        setError(err.message ?? "Failed to accept invite.");
      }
    } finally {
      setAccepting(false);
    }
  }

  async function handleRegisterAndAccept(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setRegistering(true);
    try {
      await registerAndAcceptInvite(token, password);
      setDone(true);
      setTimeout(() => router.push(`/${locale}/app/search`), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Registration failed.");
    } finally {
      setRegistering(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 size={24} className="animate-spin text-blue-500" />
      </div>
    );
  }

  if (done) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-green-200 bg-green-50 p-8 text-center">
          <Check size={32} className="text-green-600 mx-auto mb-3" />
          <h1 className="text-lg font-semibold text-green-800 mb-2">{t.joined}</h1>
          <p className="text-sm text-green-700">{t.redirecting}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <Building2 size={20} className="text-blue-600" />
          <h1 className="text-lg font-semibold text-slate-800">{t.title}</h1>
        </div>

        {error && (
          <div className="rounded bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700 mb-4">
            {error}
          </div>
        )}

        {/* ── New user: register inline ──────────────────────────────── */}
        {preview && !isLoggedIn && !preview.user_exists && !showSwitchWarning && (
          <>
            <p className="text-sm text-slate-600 mb-1">
              {sub(t.invitedToJoin, { orgName: preview.org_name })}
            </p>
            <p className="text-sm text-slate-500 mb-5">{t.setupAccount}</p>
            <form onSubmit={handleRegisterAndAccept} className="flex flex-col gap-4">
              <div>
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">{t.email}</label>
                <input
                  type="email"
                  readOnly
                  value={preview.invited_email}
                  className="mt-1 w-full rounded border border-slate-100 bg-slate-50 px-3 py-2 text-sm text-slate-500 cursor-not-allowed"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">{t.choosePassword}</label>
                <input
                  type="password"
                  required
                  autoComplete="new-password"
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  placeholder={t.minChars}
                />
              </div>
              <button
                type="submit"
                disabled={registering}
                className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {registering ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {registering ? t.creating : sub(t.createAndJoin, { orgName: preview.org_name })}
              </button>
            </form>
            <p className="mt-3 text-center text-xs text-slate-400">
              {t.alreadyHaveAccount}{" "}
              <a href={`/${locale}/login?next=${encodeURIComponent(`/${locale}/accept-invite?token=${token}`)}`} className="text-blue-600 hover:underline">
                {t.signIn}
              </a>
            </p>
          </>
        )}

        {/* ── Existing user: accept / switch warning ─────────────────── */}
        {preview && (isLoggedIn || preview.user_exists) && !showSwitchWarning && (
          <>
            <p className="text-sm text-slate-600 mb-6">
              {sub(t.invitedToJoin, { orgName: preview.org_name })}
            </p>

            {currentOrgName && (
              <div className="flex gap-2 rounded bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-700 mb-4">
                <AlertTriangle size={16} className="shrink-0 mt-0.5" />
                <span>{sub(t.orgSwitchWarning, { currentOrg: currentOrgName, orgName: preview.org_name })}</span>
              </div>
            )}

            {isLoggedIn ? (
              <button
                onClick={() => handleAccept(!!currentOrgName)}
                disabled={accepting}
                className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {accepting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {currentOrgName
                  ? sub(t.leaveAndJoin, { currentOrg: currentOrgName, orgName: preview.org_name })
                  : sub(t.join, { orgName: preview.org_name })}
              </button>
            ) : (
              <a
                href={`/${locale}/login?next=${encodeURIComponent(`/${locale}/accept-invite?token=${token}`)}`}
                className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors flex items-center justify-center gap-2"
              >
                {t.signInToAccept}
              </a>
            )}

            <p className="mt-3 text-center text-xs text-slate-400">
              {t.invitedTo} {preview.invited_email}
            </p>
          </>
        )}

        {showSwitchWarning && preview && currentOrgName && (
          <>
            <div className="flex gap-2 rounded bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-700 mb-4">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" />
              <span>{sub(t.switchWarning, { currentOrg: currentOrgName, orgName: preview.org_name })}</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setShowSwitchWarning(false)}
                className="flex-1 rounded border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
              >
                {t.cancel}
              </button>
              <button
                onClick={() => handleAccept(true)}
                disabled={accepting}
                className="flex-1 rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {accepting ? <Loader2 size={14} className="animate-spin" /> : null}
                {t.acceptAndSwitch}
              </button>
            </div>
          </>
        )}

        {!preview && !error && (
          <p className="text-sm text-slate-500">{t.loadingDetails}</p>
        )}
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense>
      <AcceptInviteContent />
    </Suspense>
  );
}
