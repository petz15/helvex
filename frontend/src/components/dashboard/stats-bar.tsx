"use client";
import type { CompanyStats } from "@/lib/types";

interface StatsBarProps {
  stats: CompanyStats;
  onFilter: (key: string, value: string) => void;
}

function Stat({ label, value, onClick, color = "default" }: {
  label: string; value: number; onClick?: () => void;
  color?: "green" | "blue" | "yellow" | "red" | "default";
}) {
  const colorMap = { green: "text-green-700", blue: "text-blue-700", yellow: "text-yellow-700", red: "text-red-700", default: "text-slate-800" };
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center px-4 py-2 hover:bg-slate-50 rounded-lg transition-colors cursor-pointer group"
    >
      <span className={`text-xl font-bold tabular-nums ${colorMap[color]}`}>{value.toLocaleString()}</span>
      <span className="text-xs text-slate-500 group-hover:text-slate-700">{label}</span>
    </button>
  );
}

export function StatsBar({ stats, onFilter }: StatsBarProps) {
  return (
    <div className="bg-white border-b border-slate-200 px-4 flex items-center gap-1 overflow-x-auto shrink-0">
      <Stat label="Total" value={stats.total} onClick={() => onFilter("", "")} />
      <div className="w-px h-8 bg-slate-200 mx-1" />
      <Stat label="Confirmed proposal" value={stats.review.confirmed_proposal ?? 0} color="green"
        onClick={() => onFilter("review_status", "confirmed_proposal")} />
      <Stat label="Potential proposal" value={stats.review.potential_proposal ?? 0} color="blue"
        onClick={() => onFilter("review_status", "potential_proposal")} />
      <Stat label="Interesting" value={stats.review.interesting ?? 0} color="yellow"
        onClick={() => onFilter("review_status", "interesting")} />
      <Stat label="Pending review" value={stats.review.pending ?? 0}
        onClick={() => onFilter("review_status", "_none")} />
      <Stat label="Rejected" value={stats.review.rejected ?? 0} color="red"
        onClick={() => onFilter("review_status", "rejected")} />
      <div className="w-px h-8 bg-slate-200 mx-1" />
      <Stat label="Proposals sent" value={stats.proposal.sent ?? 0} color="yellow"
        onClick={() => onFilter("proposal_status", "sent")} />
      <Stat label="Converted" value={stats.proposal.converted ?? 0} color="green"
        onClick={() => onFilter("proposal_status", "converted")} />
      <div className="w-px h-8 bg-slate-200 mx-1" />
      <Stat label="Google searched" value={stats.searched} color="blue"
        onClick={() => onFilter("google_searched", "yes")} />
    </div>
  );
}
