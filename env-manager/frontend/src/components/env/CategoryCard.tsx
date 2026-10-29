import * as React from "react"
import { cn } from "@/lib/utils"
import { Zap, Cloud, Shield, Database, Key, Settings, ChevronRight } from "lucide-react"
import { Card, CardContent } from "@/components/ui/Card"
import { Badge } from "@/components/ui/Badge"
import { EnvCategory } from "@/types/env"

const categoryIcons: Record<string, React.ReactNode> = {
  "Social Media": <Zap className="h-6 w-6" />,
  "AI Services": <Cloud className="h-6 w-6" />,
  "Core": <Shield className="h-6 w-6" />,
  "Database": <Database className="h-6 w-6" />,
  "Storage": <Key className="h-6 w-6" />,
  "Settings": <Settings className="h-6 w-6" />,
}

const categoryColors: Record<string, string> = {
  "Social Media": "bg-blue-500",
  "AI Services": "bg-purple-500",
  "Core": "bg-green-500",
  "Database": "bg-orange-500",
  "Storage": "bg-red-500",
  "Settings": "bg-gray-500",
}

interface CategoryCardProps {
  category: EnvCategory
  onClick: () => void
  isActive?: boolean
}

export const CategoryCard: React.FC<CategoryCardProps> = ({ category, onClick, isActive }) => {
  const requiredCount = category.variables.filter(v => v.required).length
  const sensitiveCount = category.variables.filter(v => v.sensitive).length
  const filledCount = category.variables.filter(v => v.value && v.value !== `YOUR_${v.key}_HERE` && v.value !== "CHANGE_THIS_TO_RANDOM_STRING").length

  return (
    <Card
      className={cn(
        "cursor-pointer transition-all hover:shadow-md hover:border-primary/50",
        isActive && "ring-2 ring-primary border-primary"
      )}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClick(); }}
      tabIndex={0}
      role="button"
      aria-pressed={isActive}
    >
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <div className={cn(
                "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl",
                categoryColors[category.name] || "bg-primary"
              )}>
                {categoryIcons[category.name] || <Settings className="h-6 w-6 text-white" />}
              </div>
              <div className="min-w-0">
                <h3 className="font-semibold text-lg text-foreground truncate">{category.name}</h3>
                <p className="text-sm text-muted-foreground">{category.variables.length} variables</p>
              </div>
            </div>
            
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="gap-1">
                {requiredCount} required
              </Badge>
              <Badge variant="outline" className="gap-1">
                {sensitiveCount} sensitive
              </Badge>
              <Badge variant={filledCount === category.variables.length ? "success" : "outline"} className="gap-1">
                {filledCount}/{category.variables.length} filled
              </Badge>
            </div>
          </div>
          
          <div className={cn(
            "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-muted",
            isActive && "bg-primary text-primary-foreground"
          )}>
            <ChevronRight className="h-5 w-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
