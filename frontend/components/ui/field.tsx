import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

export function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1 text-[13px] font-medium text-muted", className)}>
      {label}
      {children}
    </label>
  );
}
