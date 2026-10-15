"use client";

import { useEffect, type RefObject } from "react";

/** Owns only this workspace's visible frame; never changes body or other routes. */
export function useWorkspaceViewport(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const viewport = window.visualViewport;
        const bottom = (viewport?.height ?? window.innerHeight) + (viewport?.offsetTop ?? 0);
        const height = Math.max(180, bottom - node.getBoundingClientRect().top);
        node.style.setProperty("--workspace-height", `${height}px`);
        node.dataset.compact = String(height < 500);
      });
    };
    const observer = new ResizeObserver(measure);
    const observeHeader = () => {
      observer.disconnect();
      document.querySelectorAll(".site-masthead, .site-language-error").forEach(element => observer.observe(element));
      measure();
    };
    const mutation = new MutationObserver(observeHeader);
    mutation.observe(node.parentElement ?? document.body, { childList: true });
    observeHeader();
    window.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("scroll", measure);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      mutation.disconnect();
      window.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("scroll", measure);
      node.style.removeProperty("--workspace-height");
      delete node.dataset.compact;
    };
  }, [ref]);
}
