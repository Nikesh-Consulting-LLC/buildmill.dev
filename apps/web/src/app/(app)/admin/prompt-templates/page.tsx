"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Eye, Pencil } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  GROUP_META,
  GROUP_ORDER,
  type TemplateItem,
} from "./template-meta";

/** US-5.18: the template library as a scannable table — one row per
 * template, View/Edit opening the per-template editor page. No inline
 * editors here anymore (us-5.17's long page). */
export default function AdminPromptTemplatesPage() {
  const [items, setItems] = useState<TemplateItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/v1/admin/prompt-templates")
      .then(setItems)
      .catch((e) => setError((e as Error).message));
  }, []);

  const ordered = items
    ? [...items].sort(
        (a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group)
      )
    : null;

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Prompt templates
        </h1>
        <p className="text-sm text-muted-foreground">
          Every prompt and content template the factory serves — edits are
          effective on the very next call, no deploy.
        </p>
      </div>

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {!ordered ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <div className="min-w-0 rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Type</TableHead>
                <TableHead className="w-full max-w-0">Name</TableHead>
                <TableHead className="hidden md:table-cell">
                  Description
                </TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ordered.map((item) => (
                <TableRow key={item.key}>
                  <TableCell>
                    <Badge variant="outline" className="font-normal">
                      {GROUP_META[item.group].badge}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-medium">{item.label}</TableCell>
                  <TableCell className="hidden max-w-md truncate text-muted-foreground md:table-cell">
                    {item.description}
                  </TableCell>
                  <TableCell>
                    {item.override ? (
                      <Badge className="font-normal">Customized</Badge>
                    ) : (
                      <Badge variant="secondary" className="font-normal">
                        Factory default
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        render={
                          <Link href={`/admin/prompt-templates/${item.key}`} />
                        }
                      >
                        <Eye className="size-3.5" />
                        View
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        render={
                          <Link
                            href={`/admin/prompt-templates/${item.key}?mode=edit`}
                          />
                        }
                      >
                        <Pencil className="size-3.5" />
                        Edit
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
