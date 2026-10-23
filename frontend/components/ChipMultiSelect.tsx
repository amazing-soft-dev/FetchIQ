
"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

import { cn } from "../lib/utils";
import { controlClass } from "./ui/input";

type ChipMultiSelectProps = {
  label: string;
  helper?: string;
  placeholder: string;
  options: string[];
  value: string[];
  onChange: (next: string[]) => void;
  countNoun: string;
};

function selectedLabel(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`} selected`;
}

export function ChipMultiSelect({
  label,
  helper,
  placeholder,
  options,
  value,
  onChange,
  countNoun,
}: ChipMultiSelectProps) {
  const inputId = useId();
  const listId = useId();
  const helperId = helper ? `${inputId}-helper` : undefined;
  const countId = `${inputId}-count`;

  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const activeOptionRef = useRef<HTMLLIElement>(null);

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  const suggestions = useMemo(() => {
    const selected = new Set(value);
    const needle = query.trim().toLowerCase();

    return options.filter(
      (option) =>
        !selected.has(option) &&
        option.toLowerCase().includes(needle),
    );
  }, [options, query, value]);

  const active =
    suggestions.length === 0
      ? -1
      : Math.min(activeIndex, suggestions.length - 1);

  useEffect(() => {
    if (!open) {
      return;
    }

    activeOptionRef.current?.scrollIntoView({ block: "nearest" });
  }, [active, open, suggestions]);

  useEffect(() => {
    if (!open) {
      return;
    }

    function onPointerDown(event: Event) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener("pointerdown", onPointerDown);

    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  function add(option: string) {
    if (value.includes(option)) {
      return;
    }

    onChange([...value, option]);
    setQuery("");
    setActiveIndex(0);
    setOpen(true);
    inputRef.current?.focus();
  }

  function remove(option: string) {
    onChange(value.filter((item) => item !== option));
    inputRef.current?.focus();
  }

  function clearAll() {
    onChange([]);
    setQuery("");
    setActiveIndex(0);
    inputRef.current?.focus();
  }

  function moveActive(delta: number) {
    setActiveIndex((current) => {
      const max = Math.max(suggestions.length - 1, 0);
      const base = Math.min(Math.max(current, 0), max);

      return Math.max(0, Math.min(base + delta, max));
    });
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();

      if (!open) {
        setOpen(true);
        setActiveIndex(0);
        return;
      }

      moveActive(1);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();

      if (!open) {
        setOpen(true);
        setActiveIndex(0);
        return;
      }

      moveActive(-1);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();

      if (open && active >= 0) {
        add(suggestions[active]);
      }

      return;
    }

    if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        setOpen(false);
      }

      return;
    }

    if (event.key === "Backspace" && query === "" && value.length > 0) {
      event.preventDefault();
      onChange(value.slice(0, -1));
    }
  }

  function onBoxPointerDown(event: PointerEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;

    if (target.closest("button, input")) {
      return;
    }

    event.preventDefault();
    inputRef.current?.focus();
  }

  const describedBy = [helperId, countId].filter(Boolean).join(" ");

  return (
    <div
      className="flex min-w-0 flex-col gap-1"
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
        }
      }}
    >
      <label className="text-[13px] font-medium text-muted" htmlFor={inputId}>
        {label}
      </label>

      {helper ? (
        <p className="text-xs text-subtle" id={helperId}>
          {helper}
        </p>
      ) : null}

      <div className="relative min-w-0">
        <div className="flex flex-col gap-1" onPointerDown={onBoxPointerDown}>
          {value.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {value.map((item) => (
                <span
                  className="inline-flex items-center gap-1 rounded-full border border-line bg-header px-2 py-0.5 text-xs text-ink"
                  key={item}
                >
                  {item}
                  <button
                    type="button"
                    className="text-muted hover:text-ink"
                    aria-label={`Remove ${item}`}
                    onClick={() => remove(item)}
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </span>
              ))}
            </div>
          ) : null}

          <input
            id={inputId}
            ref={inputRef}
            className={controlClass}
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={
              open && active >= 0 ? `${listId}-opt-${active}` : undefined
            }
            aria-describedby={describedBy}
            autoComplete="off"
            spellCheck={false}
            placeholder={placeholder}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onInputKeyDown}
          />
        </div>

        {open ? (
          <ul
            className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-control border border-line bg-white p-1 shadow-panel"
            id={listId}
            role="listbox"
            aria-label={`${label} suggestions`}
          >
            {suggestions.length === 0 ? (
              <li className="px-2 py-1.5 text-sm text-subtle">No matches</li>
            ) : (
              suggestions.map((option, index) => (
                <li
                  key={option}
                  id={`${listId}-opt-${index}`}
                  ref={index === active ? activeOptionRef : undefined}
                  role="option"
                  aria-selected={index === active}
                  className={cn(
                    "cursor-pointer rounded-md px-2 py-1.5 text-sm text-ink",
                    index === active && "bg-hover",
                  )}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => add(option)}
                >
                  {option}
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>

      <div className="flex items-center justify-between text-xs text-muted">
        <span id={countId}>{selectedLabel(value.length, countNoun)}</span>
        <button type="button" className="text-accent hover:underline" onClick={clearAll}>
          Clear all
        </button>
      </div>
    </div>
  );
}
