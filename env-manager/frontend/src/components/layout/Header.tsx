import * as React from "react"
import { cn } from "@/lib/utils"
import { Sun, Moon, Bell, LogOut, Menu } from "lucide-react"
import { Button } from "@/components/ui/Button"
import { DropdownMenu, DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/DropdownMenu"
import { Tooltip } from "@/components/ui/Tooltip"
import { Avatar } from "@/components/ui/Avatar"

interface HeaderProps {
  onMenuClick: () => void
  sidebarCollapsed: boolean
  onToggleSidebar: () => void
  theme: "light" | "dark"
  onThemeChange: (theme: "light" | "dark") => void
  user?: { name: string; email?: string }
  onLogout: () => void
  notificationCount?: number
}

export const Header: React.FC<HeaderProps> = ({
  onMenuClick,
  sidebarCollapsed,
  onToggleSidebar,
  theme,
  onThemeChange,
  user = { name: "Admin", email: "admin@example.com" },
  onLogout,
  notificationCount = 0,
}) => {

  return (
    <header
      className={cn(
        "fixed right-0 top-0 z-30 h-16 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 transition-all duration-300",
        sidebarCollapsed ? "left-16" : "left-64"
      )}
      role="banner"
    >
      <div className="flex h-full items-center justify-between px-4 gap-4">
        {/* Left side - Menu toggle */}
        <div className="flex items-center gap-2">
          <Tooltip content={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"} side="bottom">
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggleSidebar}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="hidden lg:flex"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </Tooltip>
          <Button
            variant="ghost"
            size="icon"
            onClick={onMenuClick}
            aria-label="Open mobile menu"
            className="lg:hidden"
          >
            <Menu className="h-5 w-5" />
          </Button>
        </div>

        {/* Right side - Actions */}
        <div className="flex items-center gap-2">
          {/* Theme toggle */}
          <Tooltip content={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} side="bottom">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onThemeChange(theme === "dark" ? "light" : "dark")}
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            >
              {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
            </Button>
          </Tooltip>

          {/* Notifications */}
          <Tooltip content={`Notifications (${notificationCount})`} side="bottom">
            <Button variant="ghost" size="icon" aria-label="Notifications">
              <Bell className={cn("h-5 w-5", notificationCount > 0 && "text-primary")} />
              {notificationCount > 0 && (
                <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-destructive-foreground text-[10px] font-medium">
                  {notificationCount > 9 ? "9+" : notificationCount}
                </span>
              )}
            </Button>
          </Tooltip>

          {/* User menu */}
          <DropdownMenu
            trigger={
              <Button variant="ghost" size="icon" className="relative h-9 w-9 rounded-full">
                <Avatar
                  fallback={user.name.charAt(0)}
                  size="md"
                  className="h-9 w-9"
                />
              </Button>
            }
            align="end"
          >
            <DropdownMenuItem inset>
              <div className="flex flex-col space-y-1">
                <p className="font-medium text-sm">{user.name}</p>
                <p className="text-xs text-muted-foreground truncate max-w-[160px]">{user.email}</p>
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onLogout}>
              <LogOut className="mr-2 h-4 w-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}
