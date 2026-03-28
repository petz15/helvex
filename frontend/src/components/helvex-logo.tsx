interface Props {
  size?: number;
  className?: string;
}

/**
 * Helvex logo mark — 7×7 diamond grid with a cross-shaped gap in the centre
 * (evokes the Swiss flag cross).  Inherits colour via `currentColor`.
 */
export function HelvexMark({ size = 20, className }: Props) {
  // 7 evenly-spaced centres in a 24 px viewbox
  const positions = [1.7, 5.1, 8.6, 12.0, 15.4, 18.9, 22.3];
  const s = 1.3; // diamond half-size

  // Cross cells (row, col) 0-indexed — the 5-cell + shape in the middle
  const CROSS = new Set(["3,2", "3,3", "3,4", "2,3", "4,3"]);

  const d = (x: number, y: number) =>
    `M ${x} ${y - s} L ${x + s} ${y} L ${x} ${y + s} L ${x - s} ${y} Z`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      {positions.map((x, col) =>
        positions.map((y, row) => {
          if (CROSS.has(`${row},${col}`)) return null;
          return <path key={`${row}-${col}`} d={d(x, y)} fill="currentColor" />;
        })
      )}
    </svg>
  );
}
