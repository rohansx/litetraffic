import { ChevronsUpDown, FolderOpen, Monitor, Moon, Sun } from "lucide-react";
import type { Meta } from "@/api/types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar";
import { useTheme, type Theme } from "@/hooks/use-theme";

/** The block's NavUser slot: this dashboard has no user, so it shows the runs folder, version and theme. */
export function NavFooter({ meta, error }: { meta?: Meta; error?: Error }) {
  const { isMobile } = useSidebar();
  const [theme, setTheme] = useTheme();
  const folder = meta?.runs_dir ?? (error ? "Unavailable" : "Loading");
  const version = meta ? `LiteTraffic v${meta.version}` : "LiteTraffic";

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              aria-label={`Runs folder ${folder}, ${version}, theme ${theme}`}
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border bg-background">
                <FolderOpen className="size-4" aria-hidden />
              </span>
              <span className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">Runs folder</span>
                <span className="truncate font-mono text-xs text-muted-foreground" title={meta?.runs_dir}>
                  {folder}
                </span>
              </span>
              <ChevronsUpDown className="ml-auto size-4" aria-hidden />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="grid gap-0.5 font-normal">
              <span className="font-medium">{version}</span>
              <span className="font-mono text-xs break-all text-muted-foreground">{folder}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-xs text-muted-foreground">Theme</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
              <DropdownMenuRadioItem value="light">
                <Sun aria-hidden />
                Light
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark">
                <Moon aria-hidden />
                Dark
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="system">
                <Monitor aria-hidden />
                System
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
