// Owner: Team B. Small shared building blocks, styled with Tailwind (tokens in src/styles.css).
import { useId, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ChevronIcon } from "./icons";

export * from "./icons";
export { inputClass } from "./styles";
export { Combobox, normalize, type ComboOption } from "./Combobox";

/**
 * A box that shows `summary` and expands to show `children` (the details), so lists stay light.
 * The whole summary row is the toggle.
 */
export function Expandable({ summary, children, defaultOpen = false, className = "" }: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={`rounded-lg border border-line bg-canvas ${className}`}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={id}
        className="flex w-full cursor-pointer items-start gap-2 rounded-lg p-3 text-left hover:bg-surface-2/60
                   focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent">
        <ChevronIcon open={open} className="mt-0.5 size-4 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">{summary}</div>
      </button>
      <div id={id} hidden={!open} className="grid gap-2 border-t border-line p-3">
        {children}
      </div>
    </div>
  );
}

/** Label above a control; `hint` is a short muted line below, replaced by `error` when set. */
export function Field({ label, hint, error, children }: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {error ? <span className="text-xs text-bad" role="alert">{error}</span>
        : hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON: Record<Variant, string> = {
  primary: "bg-accent text-surface hover:brightness-110",
  secondary: "bg-surface border-line text-ink hover:bg-surface-2",
  danger: "bg-bad-soft text-bad hover:brightness-95",
  ghost: "bg-transparent text-accent px-1.5 hover:underline",
};

export function Button({ variant = "secondary", className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={`cursor-pointer rounded-lg border border-transparent px-3 py-1.5 text-sm transition
        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
        disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON[variant]} ${className}`}
      {...rest}
    />
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4 shadow-sm">
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions && <div className="flex gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

type Tone = "neutral" | "accent" | "good" | "warn" | "bad";

const BADGE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  accent: "bg-accent-soft text-accent",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${BADGE[tone]}`}>
      {children}
    </span>
  );
}

/** Small status dot for badges; `pulse` animates unless the user prefers reduced motion. */
export function Dot({ pulse = false }: { pulse?: boolean }) {
  return <span className={`size-[7px] rounded-full bg-current ${pulse ? "motion-safe:animate-pulse" : ""}`} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="m-0 text-muted">{children}</p>;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-2 rounded-lg bg-bad-soft px-3 py-2 text-bad">
      <span>{message}</span>
      {onRetry && <Button variant="ghost" onClick={onRetry}>Tentar outra vez</Button>}
    </div>
  );
}

export function Loading() {
  return <p className="m-0 text-muted" aria-busy="true">A carregar…</p>;
}
