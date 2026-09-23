import { FolderOpen, GitCompareArrows, Info, LayoutDashboard, ListChecks, Workflow, type LucideIcon } from "lucide-react";
import { NavLink, useLocation } from "react-router";
import { api } from "@/api/client";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useApi } from "@/lib/use-api";

const NAV: { to: string; label: string; icon: LucideIcon; end?: boolean }[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Runs", icon: ListChecks },
  { to: "/scenarios", label: "Scenarios", icon: Workflow },
  { to: "/compare", label: "Compare", icon: GitCompareArrows },
  { to: "/about", label: "About", icon: Info },
];

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className} fill="currentColor">
      <path d="M4 17h4v11H4zm7-9h4v20h-4zm7 5h4v15h-4zm7-9h4v24h-4z" />
    </svg>
  );
}

export function AppSidebar() {
  const { pathname } = useLocation();
  const { setOpenMobile } = useSidebar();
  const meta = useApi("meta", (signal) => api.meta(signal));

  return (
    <Sidebar collapsible="icon" aria-label="Main">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="LiteTraffic">
              <NavLink to="/" onClick={() => setOpenMobile(false)}>
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <BrandMark className="size-5" />
                </span>
                <span className="grid leading-tight">
                  <span className="font-heading text-lg font-semibold">LiteTraffic</span>
                  <span className="text-xs text-muted-foreground">
                    {meta.data ? `Local dashboard v${meta.data.version}` : "Local dashboard"}
                  </span>
                </span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.map(({ to, label, icon: Icon, end }) => {
                const active = end ? pathname === to : pathname === to || pathname.startsWith(`${to}/`);
                return (
                  <SidebarMenuItem key={to}>
                    <SidebarMenuButton
                      asChild
                      isActive={active}
                      tooltip={label}
                      className="data-[active=true]:shadow-[inset_2px_0_0_var(--brand)]"
                    >
                      <NavLink to={to} end={end} onClick={() => setOpenMobile(false)}>
                        <Icon aria-hidden />
                        <span>{label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 group-data-[collapsible=icon]:flex-col">
          <div className="flex min-w-0 flex-1 items-start gap-2 rounded-md px-2 py-1.5 text-xs group-data-[collapsible=icon]:hidden">
            <FolderOpen className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0">
              <p className="text-muted-foreground">Runs folder</p>
              <p className="truncate font-mono" title={meta.data?.runs_dir}>
                {meta.data?.runs_dir ?? (meta.error ? "Unavailable" : "Loading")}
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
