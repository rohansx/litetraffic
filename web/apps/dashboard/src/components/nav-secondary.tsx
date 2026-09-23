import type { ComponentProps } from "react";
import { NavLink, useLocation } from "react-router";
import { isActive, type NavItem } from "@/components/nav-main";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

export function NavSecondary({ items, ...props }: { items: NavItem[] } & ComponentProps<typeof SidebarGroup>) {
  const { pathname } = useLocation();
  const { setOpenMobile } = useSidebar();
  return (
    <SidebarGroup {...props}>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => (
            <SidebarMenuItem key={item.url}>
              <SidebarMenuButton asChild size="sm" isActive={isActive(pathname, item)} tooltip={item.title}>
                <NavLink to={item.url} onClick={() => setOpenMobile(false)}>
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
