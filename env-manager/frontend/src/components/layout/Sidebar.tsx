import * as React from "react"
import { cn } from "@/lib/utils"
import { 
  LayoutDashboard, 
  Key, 
  Database, 
  Cloud, 
  Settings, 
  Shield, 
  Zap,
  ChevronLeft,
  ChevronRight,
  Menu
} from "lucide-react"
import { EnvCategory } from "@/types/env"
import { Button } from "@/components/ui/Button"
import { Tooltip } from "@/components/ui/Tooltip"

interface SidebarProps {
  categories: EnvCategory[]
  activeCategory: string
  onCategoryChange: (category: string) => void
  collapsed?: boolean
  onToggleCollapse: () => void
}

const categoryIcons: Record<string, React.ReactNode> = {
  "Social Media": <Zap className="h-4 w-4" />,
  "AI Services": <Cloud className="h-4 w-4" />,
  "Core": <Shield className="h-4 w-4" />,
  "Database": <Database className="h-4 w-4" />,
  "Storage": <Key className="h-4 w-4" />,
  "Settings": <Settings className="h-4 w-4" />,
}

const categoryColors: Record<string, string> = {
  "Social Media": "text-blue-500",
  "AI Services": "text-purple-500",
  "Core": "text-green-500",
  "Database": "text-orange-500",
  "Storage": "text-red-500",
  "Settings": "text-gray-500",
}

export const Sidebar: React.FC<SidebarProps> = ({ 
  categories, 
  activeCategory, 
  onCategoryChange, 
  collapsed = false, 
  onToggleCollapse 
}) => {
  return (
    <>
      <Tooltip content={collapsed ? "Expand sidebar" : "Collapse sidebar"} side="right">
        <Button
          variant="ghost"
          size="icon"
          className={cn("absolute -right-2 top-4 z-10", collapsed && "left-12")}
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </Button>
      </Tooltip>
      <aside
      className={cn(
        "fixed left-0 top-0 z-40 h-screen border-r bg-card transition-all duration-300 ease-in-out flex flex-col",
        collapsed ? "w-16" : "w-64"
      )}
      aria-label="Sidebar navigation"
    >
      {/* Logo */}
      <div className={cn("flex items-center justify-center h-16 border-b px-4", collapsed && "px-0")}>
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
              <Key className="h-5 w-5 text-primary-foreground" />
            </div>
            <span className="font-semibold text-lg text-foreground">Env Manager</span>
          </div>
        )}
        {collapsed && (
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary mx-auto">
            <Key className="h-5 w-5 text-primary-foreground" />
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto p-3 space-y-1" role="navigation" aria-label="Main navigation">
        {categories.map((category) => (
          <Tooltip key={category.name} content={category.name} side="right" delayDuration={300}>
            <Button
              variant={activeCategory === category.name ? "default" : "ghost"}
              className={cn(
                "w-full justify-start gap-3",
                collapsed && "px-2"
              )}
              onClick={() => onCategoryChange(category.name)}
              aria-current={activeCategory === category.name ? "page" : undefined}
            >
              <span className={cn(categoryColors[category.name] || "", "flex-shrink-0")}>
                {categoryIcons[category.name] || <Key className="h-4 w-4" />}
              </span>
              {!collapsed && (
                <span className="truncate font-medium">{category.name}</span>
              )}
              {!collapsed && category.variables.length > 0 && (
                <span className="ml-auto text-xs px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                  {category.variables.length}
                </span>
              )}
            </Button>
          </Tooltip>
        ))}
      </nav>

      {/* Footer */}
      <div className={cn("border-t p-3", collapsed && "px-0")}>
        {!collapsed && (
          <div className="text-xs text-muted-foreground text-center">
            v1.0.0 • Social Media Marketing Stack
          </div>
        )}
      </div>
    </aside>
    </>
  )
}
