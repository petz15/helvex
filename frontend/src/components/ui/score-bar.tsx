"use client";
import { cn, scoreColor } from "@/lib/utils";

interface ScoreBarProps {
  score: number | null;
  label?: string;
  className?: string;
}

export function ScoreBar({ score, label, className }: ScoreBarProps) {
  if (score === null) return <span className="text-gray-400 text-xs">—</span>;
  return (
    <div className={cn("flex items-center gap-1.5 min-w-[4.5rem]", className)}>
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", scoreColor(score))}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs text-gray-600 w-6 text-right">{score}</span>
    </div>
  );
}
