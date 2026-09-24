import { useEffect, type ReactNode } from "react";
import { useLocation } from "react-router";
import { NAV_ITEMS } from "@/components/app-sidebar";
import { isActive } from "@/components/nav-main";
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

/** The sidebar entry this page lives under, numbered in sidebar order (detail pages share their list's number). */
function useSection(): { number: string; eyebrow: string } | undefined {
  const { pathname } = useLocation();
  const index = NAV_ITEMS.findIndex((item) => isActive(pathname, item));
  const item = NAV_ITEMS[index];
  return item && { number: String(index + 1).padStart(2, "0"), eyebrow: item.eyebrow };
}

/** dashboard-01 page body: SiteHeader over a grid-textured title band, a lane marking, then the `@container/main` column. */
export function Page({ title, documentTitle, description, crumbs = [], actions, children }: PageProps) {
  const tabTitle = documentTitle ?? (typeof title === "string" ? title : "");
  const section = useSection();
  useEffect(() => {
    document.title = tabTitle ? `${tabTitle} | LiteTraffic` : "LiteTraffic";
  }, [tabTitle]);

  return (
    <div className="@container/main flex flex-1 flex-col">
      <div className="grid-bg md:rounded-t-xl">
        <SiteHeader crumbs={crumbs} />
        <div className="flex flex-wrap items-end justify-between gap-4 px-4 pt-5 pb-6 md:pt-6 lg:px-6">
          <div className="min-w-0 space-y-3">
            {section && (
              <div className="flex items-center gap-12 [--connector-length:2.25rem]">
                <span className="section-number is-active" aria-hidden>
                  {section.number}
                </span>
                <p className="eyebrow">{section.eyebrow}</p>
              </div>
            )}
            <div className="space-y-1">
              <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
              {description && <div className="max-w-2xl text-sm text-muted-foreground">{description}</div>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      </div>
      <hr className="lane-divider" />
      <div className="flex flex-1 flex-col gap-4 px-4 py-4 md:gap-6 md:py-6 lg:px-6">{children}</div>
    </div>
  );
}
