"use client";

import { createContext, useContext } from "react";
import type { Dictionary, Locale } from "./dictionaries";

type I18nContextValue = {
  dict: Dictionary;
  locale: Locale;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({
  dict,
  locale,
  children,
}: {
  dict: Dictionary;
  locale: Locale;
  children: React.ReactNode;
}) {
  return (
    <I18nContext.Provider value={{ dict, locale }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
