// Owner: Team B. Searchable select: type to filter a scrollable list; optionally offer "create <text>".
import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { inputClass } from "./styles";

export interface ComboOption {
  id: string;
  label: string;
  hint?: string;
}

/** Lowercase without accents, so "televisao" finds "Televisão". */
export function normalize(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

export function Combobox({ options, value, onSelect, onCreate, createLabel, placeholder, ariaLabel }: {
  options: ComboOption[];
  value: string | null;
  onSelect: (id: string) => void;
  /** When set, a query with no exact match offers "createLabel(query)". */
  onCreate?: (query: string) => void;
  createLabel?: (query: string) => string;
  placeholder?: string;
  ariaLabel: string;
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const selected = options.find((o) => o.id === value);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const q = normalize(query);
  const filtered = useMemo(
    () => (q ? options.filter((o) => normalize(`${o.label} ${o.id} ${o.hint ?? ""}`).includes(q)) : options),
    [options, q],
  );
  const exact = options.some((o) => normalize(o.label) === q || normalize(o.id) === q);
  const canCreate = Boolean(onCreate && q && !exact);
  const count = filtered.length + (canCreate ? 1 : 0);

  const choose = (i: number) => {
    if (i < filtered.length) onSelect(filtered[i].id);
    else if (canCreate) onCreate?.(query.trim());
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, count - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && open && count > 0) { e.preventDefault(); choose(active); }
    else if (e.key === "Escape") { setOpen(false); setQuery(""); }
  };

  const optionId = (i: number) => `${listId}-${i}`;

  return (
    <div className="relative">
      <input
        ref={inputRef}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && count > 0 ? optionId(active) : undefined}
        className={inputClass}
        placeholder={selected ? selected.label : placeholder}
        value={open ? query : selected?.label ?? query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => { setOpen(true); setActive(0); }}
        onBlur={() => { setOpen(false); setQuery(""); }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul id={listId} role="listbox"
            className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-line bg-surface p-1
                       shadow-lg">
          {filtered.map((o, i) => (
            <li key={o.id} id={optionId(i)} role="option" aria-selected={o.id === value}
                onMouseDown={(e) => e.preventDefault()} onClick={() => choose(i)} onMouseEnter={() => setActive(i)}
                className={`cursor-pointer rounded-md px-2.5 py-1.5 text-sm ${i === active ? "bg-surface-2" : ""}
                            ${o.id === value ? "font-semibold" : ""}`}>
              {o.label}
              {o.hint && <span className="ml-1.5 text-xs text-muted">{o.hint}</span>}
            </li>
          ))}
          {canCreate && (
            <li id={optionId(filtered.length)} role="option" aria-selected={false}
                onMouseDown={(e) => e.preventDefault()} onClick={() => choose(filtered.length)}
                onMouseEnter={() => setActive(filtered.length)}
                className={`cursor-pointer rounded-md px-2.5 py-1.5 text-sm font-medium text-accent
                            ${active === filtered.length ? "bg-surface-2" : ""}
                            ${filtered.length ? "mt-1 border-t border-line pt-2" : ""}`}>
              {createLabel ? createLabel(query.trim()) : `Adicionar "${query.trim()}"`}
            </li>
          )}
          {count === 0 && <li className="px-2.5 py-1.5 text-sm text-muted">Sem resultados.</li>}
        </ul>
      )}
    </div>
  );
}
