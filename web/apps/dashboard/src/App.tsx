import type { CSSProperties } from "react";
import { BrowserRouter, Link, Outlet, Route, Routes } from "react-router";
import { AppSidebar } from "@/components/app-sidebar";
import { Page } from "@/components/page";
import { EmptyState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AboutPage } from "@/pages/about";
import { ComparePage } from "@/pages/compare";
import { OverviewPage } from "@/pages/overview";
import { RunDetailPage } from "@/pages/run-detail";
import { RunsPage } from "@/pages/runs";
import { ScenarioPage, ScenariosPage } from "@/pages/scenarios";

function Layout() {
  return (
    <TooltipProvider delayDuration={200}>
      <SidebarProvider
        style={{ "--sidebar-width": "calc(var(--spacing) * 64)", "--header-height": "calc(var(--spacing) * 12)" } as CSSProperties}
      >
        <a
          href="#content"
          className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <AppSidebar variant="inset" />
        <SidebarInset id="content">
          <Outlet />
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}

function NotFound() {
  return (
    <Page title="Page not found" crumbs={[{ label: "Not found" }]}>
      <EmptyState
        title="There is nothing at this address"
        action={
          <Button asChild variant="outline">
            <Link to="/">Go to overview</Link>
          </Button>
        }
      >
        Runs and scenarios are linked from the sidebar.
      </EmptyState>
    </Page>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:id" element={<RunDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="scenarios" element={<ScenariosPage />} />
        <Route path="scenarios/:name" element={<ScenarioPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
