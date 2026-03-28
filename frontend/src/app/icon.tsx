import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

const COLOR = "#2563eb"; // blue-600

// 7×7 grid, 7 positions mapped to 0..31
const POS = [2, 6.5, 11, 15.5, 20, 24.5, 29];
const S = 1.8; // diamond half-size

// Cross cells to leave empty: (row, col) — the + in the centre
const CROSS = new Set(["3,2", "3,3", "3,4", "2,3", "4,3"]);

function diamond(cx: number, cy: number) {
  return `M${cx},${cy - S} L${cx + S},${cy} L${cx},${cy + S} L${cx - S},${cy} Z`;
}

export default function Icon() {
  const paths: string[] = [];
  POS.forEach((x, col) => {
    POS.forEach((y, row) => {
      if (!CROSS.has(`${row},${col}`)) paths.push(diamond(x, y));
    });
  });

  return new ImageResponse(
    <div
      style={{
        width: 32,
        height: 32,
        display: "flex",
        position: "relative",
        background: "transparent",
      }}
    >
      <svg
        width="32"
        height="32"
        viewBox="0 0 32 32"
        style={{ position: "absolute", inset: 0 }}
      >
        <path d={paths.join(" ")} fill={COLOR} />
      </svg>
    </div>,
    { ...size },
  );
}
