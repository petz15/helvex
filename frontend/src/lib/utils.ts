import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scoreColor(score: number | null): string {
  if (score === null) return "bg-gray-200";
  if (score >= 70) return "bg-green-500";
  if (score >= 40) return "bg-yellow-400";
  return "bg-red-400";
}

export function scoreBadgeClass(score: number | null): string {
  if (score === null) return "bg-gray-100 text-gray-500";
  if (score >= 70) return "bg-green-100 text-green-800";
  if (score >= 40) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-700";
}

export function reviewBadgeClass(status: string | null): string {
  switch (status) {
    case "confirmed_proposal":
    case "confirmed_generic":
      return "bg-green-100 text-green-800";
    case "potential_proposal":
    case "potential_generic":
      return "bg-blue-100 text-blue-800";
    case "interesting":
      return "bg-yellow-100 text-yellow-800";
    case "rejected":
      return "bg-red-100 text-red-700";
    default:
      return "bg-gray-100 text-gray-500";
  }
}

export function proposalBadgeClass(status: string | null): string {
  switch (status) {
    case "converted":
      return "bg-green-100 text-green-800";
    case "responded":
      return "bg-blue-100 text-blue-800";
    case "sent":
      return "bg-yellow-100 text-yellow-800";
    case "rejected":
      return "bg-red-100 text-red-700";
    default:
      return "bg-gray-100 text-gray-500";
  }
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("de-CH", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-CH", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
