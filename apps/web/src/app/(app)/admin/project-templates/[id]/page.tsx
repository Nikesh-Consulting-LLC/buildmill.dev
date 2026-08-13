"use client";

// The list + editor merged into one page at /admin/project-templates
// (?id=<template>) — a left-side template list with a full-height
// Write/Preview editor on the right, replacing this separate detail route.
// Kept as a redirect so any existing link to /admin/project-templates/[id]
// still lands somewhere useful.

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function ProjectTemplateRedirect() {
  const router = useRouter();
  const params = useParams<{ id: string }>();

  useEffect(() => {
    router.replace(`/admin/project-templates?id=${params.id}`);
  }, [router, params.id]);

  return null;
}
