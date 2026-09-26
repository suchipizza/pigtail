// A tiny history-API router: the app has a handful of routes, a dependency isn't worth it.
import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

const listeners = new Set<() => void>();

export function navigate(to: string): void {
  if (to === window.location.pathname + window.location.search) return;
  window.history.pushState(null, "", to);
  listeners.forEach((l) => l());
}

export function useLocation(): { path: string; search: URLSearchParams } {
  const [, setTick] = useState(0);
  useEffect(() => {
    const update = () => setTick((t) => t + 1);
    listeners.add(update);
    window.addEventListener("popstate", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return { path: window.location.pathname, search: new URLSearchParams(window.location.search) };
}

export type Route =
  | { name: "cases" }
  | { name: "case"; id: string }
  | { name: "evidence"; id: string }
  | { name: "briefs" }
  | { name: "briefNew" }
  | { name: "brief"; id: string }
  | { name: "briefEdit"; id: string }
  | { name: "notFound" };

export function matchRoute(path: string): Route {
  if (path === "/" || path === "/cases" || path === "/cases/") return { name: "cases" };
  let m = /^\/cases\/([A-Za-z0-9_]+)\/?$/.exec(path);
  if (m?.[1]) return { name: "case", id: m[1] };
  m = /^\/evidence\/([A-Za-z0-9_]+)\/?$/.exec(path);
  if (m?.[1]) return { name: "evidence", id: m[1] };
  // D7 briefs (M12). Brief ids: lowercase letters, digits and dashes.
  if (path === "/briefs" || path === "/briefs/") return { name: "briefs" };
  if (path === "/briefs/new" || path === "/briefs/new/") return { name: "briefNew" };
  m = /^\/briefs\/([a-z0-9-]+)\/edit\/?$/.exec(path);
  if (m?.[1]) return { name: "briefEdit", id: m[1] };
  m = /^\/briefs\/([a-z0-9-]+)\/?$/.exec(path);
  if (m?.[1]) return { name: "brief", id: m[1] };
  return { name: "notFound" };
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
