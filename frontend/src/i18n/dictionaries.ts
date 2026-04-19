import "server-only";
export { SUPPORTED_LOCALES, DEFAULT_LOCALE, isLocale } from "./locales";
export type { Locale } from "./locales";

const dictionaries = {
  de: () => import("../../messages/de.json").then((m) => m.default),
  fr: () => import("../../messages/fr.json").then((m) => m.default),
  it: () => import("../../messages/it.json").then((m) => m.default),
  en: () => import("../../messages/en.json").then((m) => m.default),
};

export type Dictionary = Awaited<ReturnType<typeof dictionaries.de>>;

export async function getDictionary(locale: import("./locales").Locale): Promise<Dictionary> {
  return dictionaries[locale]();
}
