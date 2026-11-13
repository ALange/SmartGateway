import { useId } from "react";

/** SmartGateway mark: several inputs converge into a routing hub and leave as one stream. */
export default function Logo({ size = 32 }: { size?: number }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="SmartGateway">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4f86ee" />
          <stop offset="1" stopColor="#2c55b8" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${id})`} />
      <g fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 8.5h4.5c3 0 3.5 2.5 5.5 7.5M6 16h10M6 23.5h4.5c3 0 3.5-2.5 5.5-7.5" opacity=".7" />
        <path d="M19.5 16H25m-2.8-2.8L25 16l-2.8 2.8" />
      </g>
      <circle cx="17" cy="16" r="3.6" fill="#fff" />
      <circle cx="17" cy="16" r="1.4" fill="#2f5fc9" />
    </svg>
  );
}
