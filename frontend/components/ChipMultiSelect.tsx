"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

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
  const valueRef = useRef(value);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  valueRef.current = value;

  const suggestions = useMemo(() => {
    const selected = new Set(value);
    const needle = query.trim().toLowerCase();
    return options.filter((option) => !selected.has(option) && option.toLowerCase().includes(needle));
  }, [options, query, value]);

  const active = suggestions.length === 0 ? -1 : Math.min(activeIndex, suggestions.length - 1);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

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
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function commit(next: string[]) {
    valueRef.current = next;
    onChange(next);
  }

  function add(option: string) {
    if (valueRef.current.includes(option)) {
      return;
    }
    commit([...valueRef.current, option]);
    setQuery("");
    setActiveIndex(0);
    setOpen(true);
    inputRef.current?.focus();
  }

  function remove(option: string) {
    commit(valueRef.current.filter((item) => item !== option));
    inputRef.current?.focus();
  }

  function clearAll() {
    commit([]);
    setQuery("");
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
    if (event.key === "Backspace" && query === "" && valueRef.current.length > 0) {
      event.preventDefault();
      commit(valueRef.current.slice(0, -1));
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
      className="chip-select"
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
        }
      }}
    >
      <label className="chip-select-label" htmlFor={inputId}>
        {label}
      </label>
      {helper ? (
        <p className="chip-select-helper" id={helperId}>
          {helper}
        </p>
      ) : null}
      <div className="chip-anchor">
        <div className="chip-box" onPointerDown={onBoxPointerDown}>
          {value.length > 0 ? (
            <div className="chip-row">
              {value.map((item) => (
                <span className="chip" key={item}>
                  {item}
                  <button
                    type="button"
                    className="chip-remove"
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
            className="chip-input"
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={open && active >= 0 ? `${listId}-opt-${active}` : undefined}
            aria-describedby={describedBy}
            autoComplete="off"
            spellCheck={false}
            placeholder={placeholder}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onInputKeyDown}
          />
        </div>
        {open ? (
          <ul className="chip-suggest" id={listId} role="listbox" aria-label={`${label} suggestions`}>
            {suggestions.length === 0 ? (
              <li className="chip-suggest-empty">No matches</li>
            ) : (
              suggestions.map((option, index) => (
                <li
                  key={option}
                  id={`${listId}-opt-${index}`}
                  ref={index === active ? activeOptionRef : undefined}
                  role="option"
                  aria-selected={index === active}
                  className={index === active ? "is-active" : undefined}
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
      <div className="chip-meta">
        <span id={countId}>{selectedLabel(value.length, countNoun)}</span>
        <button type="button" className="chip-clear" onClick={clearAll}>
          Clear all
        </button>
      </div>
    </div>
  );
}
