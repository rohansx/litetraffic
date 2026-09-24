import type { LucideIcon } from "lucide-react";
import { NavLink, useLocation } from "react-router";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

export interface NavItem {
  title: string;
  url: string;
  icon: LucideIcon;
  /** Active only on an exact match (the index route). */
  end?: boolean;
}

export function isActive(pathname: string, { url, end }: NavItem) {
  return end ? pathname === url : pathname === url || pathname.startsWith(`${url}/`);
}

export function NavMain({ items, label }: { items: NavItem[]; label?: string }) {
  const { pathname } = useLocation();
  const { setOpenMobile } = useSidebar();
  return (
    <SidebarGroup>
      {label && <SidebarGroupLabel>{label}</SidebarGroupLabel>}
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => (
            <SidebarMenuItem key={item.url}>
              <SidebarMenuButton asChild isActive={isActive(pathname, item)} tooltip={item.title}>
                <NavLink to={item.url} end={item.end} onClick={() => setOpenMobile(false)}>
                  <item.icon aria-hidden />
                  <span>{item.title}</span>
                </NavLink>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
