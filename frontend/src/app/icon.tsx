import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

const COLOR = "#4f46e5"; // indigo-600
const SQ = 6; // square side before 45° rotation → ~8.5 px diagonal

// 3×3 checkerboard: (col+row) % 2 === 0 → filled solid, else outlined
const GRID: [cx: number, cy: number, filled: boolean][] = [
  [5, 5, true],   [16, 5, false],  [27, 5, true],
  [5, 16, false], [16, 16, true],  [27, 16, false],
  [5, 27, true],  [16, 27, false], [27, 27, true],
];

export default function Icon() {
  return new ImageResponse(
    <div
      style={{
        width: 32,
        height: 32,
        display: "flex",
        position: "relative",
      }}
    >
      {GRID.map(([cx, cy, filled], i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            width: SQ,
            height: SQ,
            left: cx - SQ / 2,
            top: cy - SQ / 2,
            background: filled ? COLOR : "transparent",
            border: filled ? "none" : `1.2px solid ${COLOR}`,
            opacity: filled ? 1 : 0.38,
            transform: "rotate(45deg)",
          }}
        />
      ))}
    </div>,
    { ...size },
  );
}
