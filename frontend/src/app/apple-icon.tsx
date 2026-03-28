import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

const COLOR = "#2563eb"; // blue-600
const BG = "#ffffff";

// 7×7 grid mapped to 0..179
const POS = [13, 39, 65, 90, 115, 141, 167];
const S = 10; // diamond half-size

// Cross cells to leave empty: (row, col) — the + in the centre
const CROSS = new Set(["3,2", "3,3", "3,4", "2,3", "4,3"]);

function diamond(cx: number, cy: number) {
  return `M${cx},${cy - S} L${cx + S},${cy} L${cx},${cy + S} L${cx - S},${cy} Z`;
}

export default function AppleIcon() {
  const paths: string[] = [];
  POS.forEach((x, col) => {
    POS.forEach((y, row) => {
      if (!CROSS.has(`${row},${col}`)) paths.push(diamond(x, y));
    });
  });

  return new ImageResponse(
    <div
      style={{
        width: 180,
        height: 180,
        display: "flex",
        position: "relative",
        background: BG,
      }}
    >
      <svg
        width="180"
        height="180"
        viewBox="0 0 180 180"
        style={{ position: "absolute", inset: 0 }}
      >
        <path d={paths.join(" ")} fill={COLOR} />
      </svg>
    </div>,
    { ...size },
  );
}
