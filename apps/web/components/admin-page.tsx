import type { ReactNode } from "react";
import { AdminNav } from "./admin-nav";

type Props = {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
};

/** One page frame keeps every admin route on the same alignment and spacing grid. */
export function AdminPage({ title, description, actions, className = "", children }: Props) {
  return (
    <main id="main-content" tabIndex={-1} className={`admin-shell ${className}`}>
      <header className="admin-header">
        <div className="page-heading-copy">
          <h1>{title}</h1>
          {description ? <p>{description}</p> : null}
        </div>
        {actions ? <div className="page-heading-actions">{actions}</div> : null}
      </header>
      <AdminNav />
      <div className="admin-content">{children}</div>
    </main>
  );
}
