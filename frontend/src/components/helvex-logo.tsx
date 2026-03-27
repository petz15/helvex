interface Props {
  size?: number;
  className?: string;
}

/**
 * Helvex diamond-grid logo mark.
 * 3×3 grid of rhombus shapes — checkerboard fill (corners + centre solid,
 * edges outlined at 40% opacity).
 */
export function HelvexMark({ size = 20, className }: Props) {
  const s = 3.2; // diamond half-size (so each diamond is 6.4 px wide in a 24 px viewbox)
  const cx = [5, 12, 19];
  const cy = [5, 12, 19];

  // checkerboard: (row+col) % 2 === 0 → filled
  const diamonds = cx.flatMap((x, col) =>
    cy.map((y, row) => ({ x, y, filled: (row + col) % 2 === 0 }))
  );

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
      {diamonds.map(({ x, y, filled }, i) =>
        filled ? (
          <path key={i} d={d(x, y)} fill="currentColor" />
        ) : (
          <path key={i} d={d(x, y)} stroke="currentColor" strokeWidth="1" opacity="0.35" />
        )
      )}
    </svg>
  );
}
