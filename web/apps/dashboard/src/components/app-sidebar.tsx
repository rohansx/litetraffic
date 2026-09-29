import type { ComponentProps } from "react";
import { GitCompareArrows, Info, LayoutDashboard, ListChecks, Workflow } from "lucide-react";
import { Link } from "react-router";
import { api } from "@/api/client";
import { NavFooter } from "@/components/nav-footer";
import { NavMain, type NavItem } from "@/components/nav-main";
import { NavSecondary } from "@/components/nav-secondary";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useApi } from "@/lib/use-api";

const NAV_MAIN: NavItem[] = [
  { title: "Overview", url: "/", icon: LayoutDashboard, end: true, eyebrow: "verdict board" },
  { title: "Runs", url: "/runs", icon: ListChecks, eyebrow: "evidence on disk" },
  { title: "Scenarios", url: "/scenarios", icon: Workflow, eyebrow: "trends per scenario" },
  { title: "Compare", url: "/compare", icon: GitCompareArrows, eyebrow: "baseline vs candidate" },
];

const NAV_SECONDARY: NavItem[] = [{ title: "About", url: "/about", icon: Info, eyebrow: "how to read it" }];

/** Every sidebar entry in order; page headers are numbered from it. */
export const NAV_ITEMS = [...NAV_MAIN, ...NAV_SECONDARY];

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className} fill="currentColor">
      <path d="M4 17h4v11H4zm7-9h4v20h-4zm7 5h4v15h-4zm7-9h4v24h-4z" />
    </svg>
  );
}

export function AppSidebar(props: ComponentProps<typeof Sidebar>) {
  const meta = useApi("meta", (signal) => api.meta(signal));
  const { setOpenMobile } = useSidebar();

  return (
    <Sidebar collapsible="icon" aria-label="Main" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="LiteTraffic">
              <Link to="/" aria-label="LiteTraffic home" onClick={() => setOpenMobile(false)}>
                <span className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                  <BrandMark className="size-4" />
                </span>
                <span className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-heading font-semibold">LiteTraffic</span>
                  <span className="truncate text-xs text-muted-foreground">Local dashboard</span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain label="Verification" items={NAV_MAIN} />
        <NavSecondary items={NAV_SECONDARY} className="mt-auto" />
      </SidebarContent>
      <SidebarFooter>
        <NavFooter meta={meta.data} error={meta.error} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
