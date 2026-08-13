"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

export type GithubRepo = { full_name: string; default_branch: string };

const CACHE_KEY = "github_repos_cache_v1";

type Cache = { repos: GithubRepo[]; fetchedAt: number };

function readCache(): Cache | null {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as Cache) : null;
  } catch {
    return null;
  }
}

function writeCache(repos: GithubRepo[]) {
  try {
    window.localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ repos, fetchedAt: Date.now() } satisfies Cache)
    );
  } catch {
    // Storage blocked or full — the list just won't be cached for next time.
  }
}

/** Repo-name pickers hit GitHub through the api on every open, which is
 * slow. Cache the list in localStorage keyed across the whole app so a
 * picker shows instantly from the last fetch, while `reload` bypasses the
 * cache, re-fetches from GitHub, and refreshes it for next time. */
export function useGithubRepos(active: boolean) {
  // Lazy-initialized straight from localStorage — a synchronous read, not a
  // fetch, so the cached list is there on the very first render with no
  // effect (and no loading flash) involved.
  const [cache] = useState<Cache | null>(() =>
    typeof window === "undefined" ? null : readCache()
  );
  const [repos, setRepos] = useState<GithubRepo[] | null>(cache?.repos ?? null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(cache?.fetchedAt ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: GithubRepo[] = await apiFetch("/api/v1/github/repos");
      setRepos(data);
      writeCache(data);
      setFetchedAt(Date.now());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active || startedRef.current || cache) return;
    startedRef.current = true;
    reload();
  }, [active, cache, reload]);

  return { repos, loading, error, fetchedAt, reload };
}
