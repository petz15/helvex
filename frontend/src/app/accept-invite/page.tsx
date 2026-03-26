"use client";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Building2, Loader2, AlertTriangle, Check } from "lucide-react";
import { fetchCurrentUser, fetchInvitePreview, acceptInvite, type InvitePreview } from "@/lib/api";

function AcceptInviteContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token") ?? "";

  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [done, setDone] = useState(false);

  // org switch warning state
  const [currentOrgName, setCurrentOrgName] = useState<string | null>(null);
  const [showSwitchWarning, setShowSwitchWarning] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("No invite token found in URL.");
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
      setTimeout(() => router.push("/app/dashboard"), 1500);
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
          <h1 className="text-lg font-semibold text-green-800 mb-2">Joined!</h1>
          <p className="text-sm text-green-700">Redirecting to dashboard…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <Building2 size={20} className="text-blue-600" />
          <h1 className="text-lg font-semibold text-slate-800">Organization Invite</h1>
        </div>

        {error && (
          <div className="rounded bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700 mb-4">
            {error}
          </div>
        )}

        {preview && !showSwitchWarning && (
          <>
            <p className="text-sm text-slate-600 mb-6">
              You've been invited to join{" "}
              <strong className="text-slate-900">{preview.org_name}</strong>.
            </p>

            {currentOrgName && (
              <div className="flex gap-2 rounded bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-700 mb-4">
                <AlertTriangle size={16} className="shrink-0 mt-0.5" />
                <span>
                  You're currently in <strong>{currentOrgName}</strong>. Accepting will move you to{" "}
                  <strong>{preview.org_name}</strong>.
                </span>
              </div>
            )}

            <button
              onClick={() => handleAccept(!!currentOrgName)}
              disabled={accepting}
              className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
            >
              {accepting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              {currentOrgName ? `Leave ${currentOrgName} & join ${preview.org_name}` : `Join ${preview.org_name}`}
            </button>

            <p className="mt-3 text-center text-xs text-slate-400">
              Invited to: {preview.invited_email}
            </p>
          </>
        )}

        {showSwitchWarning && preview && (
          <>
            <div className="flex gap-2 rounded bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-700 mb-4">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" />
              <span>
                You are currently in <strong>{currentOrgName}</strong>. Accepting this invite will
                move you to <strong>{preview.org_name}</strong>. You'll lose access to your data in{" "}
                {currentOrgName}.
              </span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setShowSwitchWarning(false)}
                className="flex-1 rounded border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleAccept(true)}
                disabled={accepting}
                className="flex-1 rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {accepting ? <Loader2 size={14} className="animate-spin" /> : null}
                Accept & switch
              </button>
            </div>
          </>
        )}

        {!preview && !error && (
          <p className="text-sm text-slate-500">
            Please{" "}
            <a href={`/login?next=/accept-invite?token=${encodeURIComponent(token)}`} className="text-blue-600 hover:underline">
              sign in
            </a>{" "}
            or{" "}
            <a href={`/register?invite=${encodeURIComponent(token)}`} className="text-blue-600 hover:underline">
              create an account
            </a>{" "}
            to accept this invite.
          </p>
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
