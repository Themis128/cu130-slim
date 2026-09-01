import * as React from "react"
import { cn } from "@/lib/utils"
import { Eye, EyeOff, Copy, AlertTriangle, CheckCircle2, Edit2, X } from "lucide-react"
import { 
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell 
} from "@/components/ui/Table"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Input"
import { Badge } from "@/components/ui/Badge"
import { Switch } from "@/components/ui/Switch"
import { Tooltip } from "@/components/ui/Tooltip"
import { EnvVariable } from "@/types/env"

interface EnvTableProps {
  variables: EnvVariable[]
  onVariableChange: (key: string, value: string) => void
  onMaskToggle: (key: string) => void
  showValues: boolean
  dirty: boolean
}

const categoryColors: Record<string, string> = {
  "Social Media": "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  "AI Services": "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  "Core": "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  "Database": "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  "Storage": "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  "Settings": "bg-gray-100 text-gray-800 dark:bg-gray-900/30 dark:text-gray-400",
}

export const EnvTable: React.FC<EnvTableProps> = ({
  variables,
  onVariableChange,
  onMaskToggle,
  showValues,
}) => {
  const [editingKey, setEditingKey] = React.useState<string | null>(null)
  const [editValue, setEditValue] = React.useState<string>("")

  const handleEdit = (variable: EnvVariable) => {
    setEditingKey(variable.key)
    setEditValue(variable.value)
  }

  const handleSave = (key: string) => {
    onVariableChange(key, editValue)
    setEditingKey(null)
    setEditValue("")
  }

  const handleCancel = () => {
    setEditingKey(null)
    setEditValue("")
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSave(editingKey!)
    if (e.key === "Escape") handleCancel()
  }

  const handleCopy = (value: string) => {
    navigator.clipboard.writeText(value)
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8"></TableHead>
            <TableHead className="min-w-[180px]">Variable</TableHead>
            <TableHead>Value</TableHead>
            <TableHead className="w-32">Category</TableHead>
            <TableHead className="w-24">Required</TableHead>
            <TableHead className="w-24">Sensitive</TableHead>
            <TableHead className="w-48">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {variables.map((variable) => {
            const isEditing = editingKey === variable.key
            const isMasked = !showValues && variable.sensitive && (variable.masked !== false)
            const displayValue = isMasked ? variable.masked_value : variable.value

            return (
              <TableRow key={variable.key} className={cn(isEditing && "bg-muted/50")}>
                {/* Drag handle / indicator */}
                <TableCell className="text-muted-foreground">
                  {variable.required && (
                    <Tooltip content="Required" side="top">
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                    </Tooltip>
                  )}
                  {!variable.required && variable.sensitive && (
                    <Tooltip content="Optional sensitive" side="top">
                      <AlertTriangle className="h-4 w-4 text-gray-400" />
                    </Tooltip>
                  )}
                </TableCell>

                {/* Variable name */}
                <TableCell>
                  <div className="font-mono text-sm font-medium text-foreground">{variable.key}</div>
                  <div className="text-xs text-muted-foreground truncate max-w-[200px]">
                    {variable.description}
                  </div>
                </TableCell>

                {/* Value */}
                <TableCell className="max-w-[300px]">
                  {isEditing ? (
                    <div className="flex items-center gap-2">
                      <Input
                        type={isMasked ? "password" : "text"}
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        autoFocus
                        className="flex-1 min-w-0"
                        placeholder="Enter value..."
                      />
                      <Button size="sm" onClick={() => onMaskToggle(variable.key)} variant="ghost">
                        {isMasked ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <code className={cn(
                        "flex-1 font-mono text-sm px-2 py-1 rounded bg-muted",
                        isMasked ? "font-normal" : ""
                      )}>
                        {displayValue}
                      </code>
                      <Tooltip content="Copy to clipboard" side="top">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleCopy(variable.value)}
                          aria-label="Copy value"
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                      </Tooltip>
                      {variable.sensitive && (
                        <Tooltip content={isMasked ? "Show value" : "Hide value"} side="top">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => onMaskToggle(variable.key)}
                            aria-label={isMasked ? "Show value" : "Hide value"}
                          >
                            {isMasked ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </Button>
                        </Tooltip>
                      )}
                    </div>
                  )}
                </TableCell>

                {/* Category */}
                <TableCell>
                  <Badge variant="outline" className={cn(categoryColors[variable.category] || "")}>
                    {variable.category}
                  </Badge>
                </TableCell>

                {/* Required */}
                <TableCell>
                  {variable.required ? (
                    <Badge variant="success" className="gap-1">
                      <CheckCircle2 className="h-3 w-3" />
                      Required
                    </Badge>
                  ) : (
                    <Badge variant="secondary">Optional</Badge>
                  )}
                </TableCell>

                {/* Sensitive */}
                <TableCell>
                  <Switch
                    checked={variable.sensitive}
                    onCheckedChange={() => {}} // Read-only
                    disabled
                    aria-label="Sensitive"
                  />
                </TableCell>

                {/* Actions */}
                <TableCell>
                  <div className="flex items-center gap-1">
                    {isEditing ? (
                      <>
                        <Tooltip content="Save" side="top">
                          <Button
                            size="sm"
                            variant="default"
                            onClick={() => handleSave(variable.key)}
                            aria-label="Save"
                          >
                            <CheckCircle2 className="h-4 w-4" />
                          </Button>
                        </Tooltip>
                        <Tooltip content="Cancel" side="top">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={handleCancel}
                            aria-label="Cancel"
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        </Tooltip>
                      </>
                    ) : (
                      <Tooltip content="Edit" side="top">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleEdit(variable)}
                          aria-label="Edit"
                        >
                          <Edit2 className="h-4 w-4" />
                        </Button>
                      </Tooltip>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      
      {variables.length === 0 && (
        <div className="flex flex-col items-center justify-center p-12 text-center">
          <AlertTriangle className="h-12 w-12 text-muted-foreground/50 mb-4" />
          <h3 className="text-lg font-medium text-foreground mb-1">No variables found</h3>
          <p className="text-sm text-muted-foreground">No environment variables in this category</p>
        </div>
      )}
    </div>
  )
}