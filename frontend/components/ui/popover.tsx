"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export function PopoverContent({
  className,
  align = "start",
  sideOffset = 4,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 w-64 rounded-control border border-line bg-white p-1 shadow-panel",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
