"use client";

import { Checkbox as BaseCheckbox } from "@base-ui/react/checkbox";
import { Check, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

export function Checkbox({
  className,
  indeterminate,
  onCheckedChange,
  ...props
}: {
  className?: string;
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  "aria-label"?: string;
}) {
  return (
    <BaseCheckbox.Root
      indeterminate={indeterminate}
      onCheckedChange={(checked) => onCheckedChange?.(checked)}
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-input bg-background shadow-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 data-[checked]:border-primary data-[checked]:bg-primary data-[checked]:text-primary-foreground data-[indeterminate]:border-primary data-[indeterminate]:bg-primary data-[indeterminate]:text-primary-foreground",
        className
      )}
      {...props}
    >
      <BaseCheckbox.Indicator className="flex items-center justify-center text-current">
        {indeterminate ? <Minus className="size-3" /> : <Check className="size-3" />}
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  );
}
