"use client";

import { useState } from "react";
import useSWR from "swr";
import { useSWRConfig } from "swr";
import {
  addCurrentUserBillingAddress,
  deleteCurrentUserBillingAddress,
  fetchCurrentUserBillingAddresses,
  setCurrentUserDefaultBillingAddress,
} from "@/lib/api";

const inputCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";

const emptyForm = {
  label: "",
  first_name: "",
  last_name: "",
  street: "",
  number: "",
  postal_code: "",
  city: "",
  country: "CH",
  company_name: "",
};

export function AddressBookManager({ returnTo }: { returnTo?: string | null }) {
  const { data, mutate, isLoading } = useSWR("billing-addresses", fetchCurrentUserBillingAddresses);
  const { mutate: mutateGlobal } = useSWRConfig();
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  function flash(kind: "success" | "error", message: string) {
    setBanner({ kind, message });
    setTimeout(() => setBanner(null), 4500);
  }

  async function onAddAddress(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await addCurrentUserBillingAddress({
        label: form.label.trim() || undefined,
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        street: form.street.trim(),
        number: form.number.trim(),
        postal_code: form.postal_code.trim(),
        city: form.city.trim(),
        country: form.country.trim().toUpperCase(),
        company_name: form.company_name.trim() || undefined,
        make_default: true,
      });
      if (returnTo) {
        window.location.href = returnTo;
        return;
      }
      await mutate();
      await mutateGlobal("me");
      setForm(emptyForm);
      flash("success", "Address saved. It is now the default for checkout.");
    } catch (err) {
      flash("error", err instanceof Error ? err.message : "Failed to save address");
    } finally {
      setSaving(false);
    }
  }

  async function onSetDefault(addressId: string) {
    setBusyId(addressId);
    try {
      await setCurrentUserDefaultBillingAddress(addressId);
      await mutate();
      await mutateGlobal("me");
    } catch (err) {
      flash("error", err instanceof Error ? err.message : "Failed to set default address");
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(addressId: string) {
    setBusyId(addressId);
    try {
      await deleteCurrentUserBillingAddress(addressId);
      await mutate();
      await mutateGlobal("me");
    } catch (err) {
      flash("error", err instanceof Error ? err.message : "Failed to delete address");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-3">
      {banner && (
        <div
          role="status"
          className={`rounded-lg border px-4 py-3 text-sm ${
            banner.kind === "success"
              ? "border-green-200 bg-green-50 text-green-800"
              : "border-red-200 bg-red-50 text-red-800"
          }`}
        >
          {banner.message}
        </div>
      )}

      <div className="rounded-lg border border-slate-100 bg-slate-50 p-4 space-y-3">
        <div>
          <p className="text-sm font-semibold text-slate-800">Add billing address</p>
          <p className="text-xs text-slate-500 mt-0.5">Saved addresses can be reused in checkout. Saving clears the form.</p>
        </div>
        <form onSubmit={onAddAddress} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              className={inputCls + " col-span-2"}
              value={form.label}
              onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
              placeholder="Label (e.g. Office, Home)"
            />
            <input
              className={inputCls}
              value={form.first_name}
              onChange={(e) => setForm((f) => ({ ...f, first_name: e.target.value }))}
              placeholder="First name"
              required
            />
            <input
              className={inputCls}
              value={form.last_name}
              onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
              placeholder="Last name"
              required
            />
            <input
              className={inputCls + " col-span-2"}
              value={form.company_name}
              onChange={(e) => setForm((f) => ({ ...f, company_name: e.target.value }))}
              placeholder="Company name (optional)"
            />
            <input
              className={inputCls + " col-span-2"}
              value={form.street}
              onChange={(e) => setForm((f) => ({ ...f, street: e.target.value }))}
              placeholder="Street"
              required
            />
            <input
              className={inputCls}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              placeholder="Number"
              required
            />
            <input
              className={inputCls}
              value={form.postal_code}
              onChange={(e) => setForm((f) => ({ ...f, postal_code: e.target.value }))}
              placeholder="Postal code"
              required
            />
            <input
              className={inputCls}
              value={form.city}
              onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))}
              placeholder="City"
              required
            />
            <input
              className={inputCls}
              value={form.country}
              onChange={(e) => setForm((f) => ({ ...f, country: e.target.value }))}
              placeholder="Country code"
              required
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? "Saving..." : "Save address"}
          </button>
        </form>
      </div>

      <div className="rounded-lg border border-slate-100 bg-white p-4">
        <p className="text-sm font-semibold text-slate-800">Saved addresses</p>
        <div className="mt-3 space-y-2">
          {isLoading && <p className="text-xs text-slate-400">Loading addresses...</p>}
          {!isLoading && (!data || data.addresses.length === 0) && (
            <p className="text-xs text-slate-500">No addresses saved yet.</p>
          )}
          {data?.addresses.map((addr) => {
            const isDefault = data.default_id === addr.id;
            return (
              <div key={addr.id} className="rounded-lg border border-slate-100 p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium text-slate-800 truncate">
                      {addr.label || `${addr.first_name} ${addr.last_name}`}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {addr.first_name} {addr.last_name}, {addr.street} {addr.number}, {addr.postal_code} {addr.city}, {addr.country}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {isDefault ? (
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Default</span>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === addr.id}
                        onClick={() => onSetDefault(addr.id)}
                        className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-60"
                      >
                        Use by default
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busyId === addr.id}
                      onClick={() => onDelete(addr.id)}
                      className="text-xs text-red-500 hover:text-red-700 disabled:opacity-60"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
