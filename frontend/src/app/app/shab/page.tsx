import type { Metadata } from "next";
import { ShabClient } from "./shab-client";

export const metadata: Metadata = { title: "SHAB Imports" };

export default function ShabPage() {
  return <ShabClient />;
}
