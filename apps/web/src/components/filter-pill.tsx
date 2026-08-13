"use client";

import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export const FILTER_PILL_ANY = "any";

/**
 * The pill-dropdown filter style from Work Items' status filter
 * (issue-views.tsx StatusFilter), generalized so any page's row of filters
 * can look the same instead of falling back to a plain `<Select>`.
 */
export function FilterPill({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: React.ReactNode }[];
  onChange: (next: string) => void;
}) {
  const active =
    value !== FILTER_PILL_ANY ? options.find((o) => o.value === value) : undefined;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          active
            ? "border-primary bg-primary text-primary-foreground"
            : "border-input text-muted-foreground hover:bg-muted"
        )}
      >
        {active ? active.label : label}
        <ChevronDown className="size-3" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(v) => typeof v === "string" && onChange(v)}
        >
          <DropdownMenuRadioItem value={FILTER_PILL_ANY}>{label}</DropdownMenuRadioItem>
          {options.map((o) => (
            <DropdownMenuRadioItem key={o.value} value={o.value}>
              {o.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
