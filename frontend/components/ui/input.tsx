import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

export const controlClass =
  "h-[34px] w-full rounded-control border border-line bg-white px-3 text-sm font-normal text-ink outline-none transition-colors placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:opacity-50";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(controlClass, className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-28 w-full rounded-control border border-line bg-white px-3 py-2 text-sm text-ink outline-none transition-colors placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20",
        className,
      )}
      {...props}
    />
  );
}
