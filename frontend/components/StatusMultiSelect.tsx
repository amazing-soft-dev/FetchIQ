"use client";

import { useId, useState } from "react";

import { cn } from "../lib/utils";
import { Checkbox } from "./ui/checkbox";
import { controlClass } from "./ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

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
  const labelId = useId();
  const listId = useId();
  const labels = new Map(options.map((option) => [option.id, option.label]));

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span id={labelId} className="text-[13px] font-medium text-muted">
        Status
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          type="button"
          className={cn(controlClass, "justify-between text-left")}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-labelledby={labelId}
        >
          {summary(value, labels)}
        </PopoverTrigger>
        <PopoverContent className="w-56">
          <ul id={listId} role="listbox" aria-multiselectable="true" aria-labelledby={labelId}>
            {options.map((option) => {
              const checked = value.includes(option.id);
              return (
                <li key={option.id} role="option" aria-selected={checked}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-ink hover:bg-hover">
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => onToggle(option.id)}
                    />
                    {option.label}
                  </label>
                </li>
              );
            })}
          </ul>
        </PopoverContent>
      </Popover>
    </div>
  );
}
