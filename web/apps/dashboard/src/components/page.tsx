import { useEffect, type ReactNode } from "react";
import { SiteHeader, type Crumb } from "@/components/site-header";

export type { Crumb };

interface PageProps {
  title: ReactNode;
  /** Plain-text title for the browser tab when `title` is not a string. */
  documentTitle?: string;
  description?: ReactNode;
  crumbs?: Crumb[];
  actions?: ReactNode;
  children: ReactNode;
}

/** dashboard-01 page body: SiteHeader, then an `@container/main` column with 4/6 spacing and lg:px-6 gutters. */
export function Page({ title, documentTitle, description, crumbs = [], actions, children }: PageProps) {
  const tabTitle = documentTitle ?? (typeof title === "string" ? title : "");
  useEffect(() => {
    document.title = tabTitle ? `${tabTitle} | LiteTraffic` : "LiteTraffic";
  }, [tabTitle]);

  return (
    <>
      <SiteHeader crumbs={crumbs} />
      <div className="@container/main flex flex-1 flex-col gap-4 px-4 py-4 md:gap-6 md:py-6 lg:px-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            {description && <div className="max-w-2xl text-sm text-muted-foreground">{description}</div>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </>
  );
}
