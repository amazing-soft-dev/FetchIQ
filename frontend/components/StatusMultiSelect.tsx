"use client";

import { useEffect, useId, useRef, useState } from "react";

type StatusOption = {
  id: string;
  label: string;
};

type StatusMultiSelectProps = {
  options: StatusOption[];
  value: string[];
  onToggle: (id: string) => void;
};

function summary(value: string[], labels: Map<string, string>): string {
  if (value.length === 0) {
    return "Any";
  }
  if (value.length === 1) {
    return labels.get(value[0]) ?? value[0];
  }
  return `${value.length} statuses`;
}

export function StatusMultiSelect({ options, value, onToggle }: StatusMultiSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const labelId = useId();
  const listId = useId();
  const labels = new Map(options.map((option) => [option.id, option.label]));

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="menu-select" ref={rootRef}>
      <span id={labelId}>Status</span>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={labelId}
        onClick={() => setOpen((current) => !current)}
      >
        {summary(value, labels)}
      </button>
      {open ? (
        <ul id={listId} className="menu-select-list" role="listbox" aria-multiselectable="true">
          {options.map((option) => {
            const checked = value.includes(option.id);
            return (
              <li key={option.id} role="option" aria-selected={checked}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(option.id)}
                  />
                  {option.label}
                </label>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
