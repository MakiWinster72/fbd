"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** Native modal semantics keep the background inert and contain keyboard focus. */
export function Modal({
  children,
  label,
  className = "",
  open = true,
  onClose,
}: {
  children: ReactNode;
  label: string;
  className?: string;
  open?: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<Element | null>(null);
  const previousOverflow = useRef<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const restore = () => {
      dialog.close();
      if (previousOverflow.current !== null) {
        document.body.style.overflow = previousOverflow.current;
        previousOverflow.current = null;
      }
      if (
        previousFocus.current instanceof HTMLElement &&
        previousFocus.current.isConnected
      )
        previousFocus.current.focus({ preventScroll: true });
    };
    const drawer = className.split(" ").includes("workspace-nav-drawer");
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let animation: Animation | undefined;
    if (open) {
      if (!dialog.open) {
        previousFocus.current = document.activeElement;
        previousOverflow.current = document.body.style.overflow;
        dialog.showModal();
        document.body.style.overflow = "hidden";
        dialog
          .querySelector<HTMLElement>("[data-initial-focus]")
          ?.focus({ preventScroll: true });
      }
      if (drawer && !reducedMotion)
        animation = dialog.animate(
          [{ transform: "translateX(-100%)" }, { transform: "translateX(0)" }],
          { duration: 220, easing: "cubic-bezier(.22,1,.36,1)" },
        );
    } else if (dialog.open) {
      if (drawer && !reducedMotion) {
        animation = dialog.animate(
          [{ transform: "translateX(0)" }, { transform: "translateX(-100%)" }],
          { duration: 180, easing: "ease-in", fill: "forwards" },
        );
        animation.onfinish = restore;
      } else restore();
    }
    return () => {
      if (animation) {
        animation.onfinish = null;
        animation.cancel();
      }
    };
  }, [open, className]);

  useEffect(
    () => () => {
      ref.current?.close();
      if (previousOverflow.current !== null)
        document.body.style.overflow = previousOverflow.current;
      if (
        previousFocus.current instanceof HTMLElement &&
        previousFocus.current.isConnected
      )
        previousFocus.current.focus({ preventScroll: true });
    },
    [],
  );

  return (
    <dialog
      ref={ref}
      className={`modal ${className}`}
      aria-label={label}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          onClose();
      }}
    >
      {children}
    </dialog>
  );
}
