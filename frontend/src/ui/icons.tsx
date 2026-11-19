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

export const ChevronIcon = ({ open = false, className = "size-4", ...p }: IconProps & { open?: boolean }) => (
  <Icon className={`${className} transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} {...p}>
    <path d="M9 6l6 6-6 6" />
  </Icon>
);
