"use client";
import { CheckCircle2, Zap, Clock, XCircle, Send, BadgeCheck, Search } from "lucide-react";
import type { CompanyStats } from "@/lib/types";
import { cn } from "@/lib/utils";

interface StatsBarProps {
  stats: CompanyStats;
  onFilter: (key: string, value: string) => void;
  activeKey?: string;
  activeValue?: string;
}

function Stat({ label, value, onClick, color = "default", icon, active }: {
  label: string; value: number; onClick?: () => void;
  color?: "green" | "blue" | "yellow" | "red" | "default";
  icon?: React.ReactNode;
  active?: boolean;
}) {
  const bgMap = {
    green: "bg-green-50",
    blue: "bg-blue-50",
    yellow: "bg-yellow-50",
    red: "bg-red-50",
    default: "bg-slate-50",
  };
  const textMap = { green: "text-green-800", blue: "text-blue-800", yellow: "text-yellow-800", red: "text-red-800", default: "text-slate-800" };
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors cursor-pointer group whitespace-nowrap",
        bgMap[color],
        "border-slate-200 hover:border-slate-300",
        active && "border-slate-500",
      )}
    >
      <div className={cn("shrink-0", textMap[color])}>
        {icon}
      </div>
      <div className="flex flex-col items-start leading-tight">
        <span className={cn("text-sm font-bold tabular-nums", textMap[color])}>{value.toLocaleString()}</span>
        <span className={cn("text-xs text-slate-600 group-hover:text-slate-800", active && "underline")}>{label}</span>
      </div>
    </button>
  );
}

export function StatsBar({ stats, onFilter, activeKey, activeValue }: StatsBarProps) {
  return (
    <div className="bg-white border-b border-slate-200 px-4 py-2 flex items-center gap-2 overflow-x-auto shrink-0">
      <Stat
        label="Total"
        value={stats.total}
        onClick={() => onFilter("", "")}
        icon={<BadgeCheck size={16} />}
        active={!activeKey}
      />
      <div className="w-px h-7 bg-slate-200 mx-1" />
      <Stat
        label="Confirmed proposal"
        value={stats.review.confirmed_proposal ?? 0}
        color="green"
        icon={<CheckCircle2 size={16} />}
        active={activeKey === "review_status" && activeValue === "confirmed_proposal"}
        onClick={() => onFilter("review_status", "confirmed_proposal")}
      />
      <Stat
        label="Potential proposal"
        value={stats.review.potential_proposal ?? 0}
        color="blue"
        icon={<Zap size={16} />}
        active={activeKey === "review_status" && activeValue === "potential_proposal"}
        onClick={() => onFilter("review_status", "potential_proposal")}
      />
      <Stat
        label="Interesting"
        value={stats.review.interesting ?? 0}
        color="yellow"
        icon={<Zap size={16} />}
        active={activeKey === "review_status" && activeValue === "interesting"}
        onClick={() => onFilter("review_status", "interesting")}
      />
      <Stat
        label="Pending review"
        value={stats.review.pending ?? 0}
        icon={<Clock size={16} />}
        active={activeKey === "review_status" && activeValue === "_none"}
        onClick={() => onFilter("review_status", "_none")}
      />
      <Stat
        label="Rejected"
        value={stats.review.rejected ?? 0}
        color="red"
        icon={<XCircle size={16} />}
        active={activeKey === "review_status" && activeValue === "rejected"}
        onClick={() => onFilter("review_status", "rejected")}
      />
      <div className="w-px h-7 bg-slate-200 mx-1" />
      <Stat
        label="Proposals sent"
        value={stats.proposal.sent ?? 0}
        color="yellow"
        icon={<Send size={16} />}
        active={activeKey === "proposal_status" && activeValue === "sent"}
        onClick={() => onFilter("proposal_status", "sent")}
      />
      <Stat
        label="Converted"
        value={stats.proposal.converted ?? 0}
        color="green"
        icon={<CheckCircle2 size={16} />}
        active={activeKey === "proposal_status" && activeValue === "converted"}
        onClick={() => onFilter("proposal_status", "converted")}
      />
      <div className="w-px h-7 bg-slate-200 mx-1" />
      <Stat
        label="Google searched"
        value={stats.searched}
        color="blue"
        icon={<Search size={16} />}
        active={activeKey === "google_searched" && activeValue === "yes"}
        onClick={() => onFilter("google_searched", "yes")}
      />
    </div>
  );
}
