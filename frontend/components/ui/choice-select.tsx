"use client";

import { Field } from "./field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./select";

type Option = {
  value: string;
  label: string;
};

export function ChoiceSelect({
  label,
  value,
  onValueChange,
  options,
  ariaLabel,
  triggerClassName,
}: {
  label?: string;
  value: string;
  onValueChange: (value: string) => void;
  options: Option[];
  ariaLabel?: string;
  triggerClassName?: string;
}) {
  const control = (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger aria-label={ariaLabel ?? label} className={triggerClassName}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  if (!label) {
    return control;
  }

  return <Field label={label}>{control}</Field>;
}
