// Owner: Team B. Inline SVG icons (stroke = currentColor, so they follow the theme). No emoji in the UI.
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, className = "size-5", ...rest }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" className={className} {...rest}>
      {children}
    </svg>
  );
}

export const MicIcon = (p: IconProps) => (
  <Icon {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Icon>
);

export const StopIcon = (p: IconProps) => (
  <Icon {...p}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" /></Icon>
);

export const SendIcon = (p: IconProps) => (
  <Icon {...p}><path d="M5 12h13M13 6l6 6-6 6" /></Icon>
);

export const SunIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Icon>
);

export const MoonIcon = (p: IconProps) => (
  <Icon {...p}><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></Icon>
);

export const ChevronIcon = ({ open = false, className = "size-4", ...p }: IconProps & { open?: boolean }) => (
  <Icon className={`${className} transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} {...p}>
    <path d="M9 6l6 6-6 6" />
  </Icon>
);

export const RefreshIcon = (p: IconProps) => (
  <Icon {...p}><path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4" /></Icon>
);

export const BoltIcon = (p: IconProps) => (
  <Icon {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7z" /></Icon>
);

export const CheckIcon = (p: IconProps) => (
  <Icon {...p}><path d="M5 12.5l4.5 4.5L19 7" /></Icon>
);

export const PersonIcon = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="7" r="3.5" /><path d="M5 21a7 7 0 0 1 14 0" /></Icon>
);
