import * as React from "react"
import { cn } from "@/lib/utils"

export interface SwitchProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  checked?: boolean
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  label?: string
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ className, checked, onCheckedChange, disabled, label, id, ...props }, ref) => {
    const switchId = id || `switch-${Math.random().toString(36).substr(2, 9)}`

    return (
      <div className="flex items-center space-x-2">
        <button
          ref={ref}
          role="switch"
          aria-checked={checked}
          aria-disabled={disabled}
          id={switchId}
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            checked ? "bg-primary border-primary" : "bg-muted border-border",
            className
          )}
          onClick={() => !disabled && onCheckedChange?.(!checked)}
          disabled={disabled}
          {...props}
        >
          <span
            className={cn(
              "pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform",
              checked ? "translate-x-5" : "translate-x-0"
            )}
          />
        </button>
        {label && (
          <label htmlFor={switchId} className="text-sm font-medium text-foreground">
            {label}
          </label>
        )}
      </div>
    )
  }
)
Switch.displayName = "Switch"

export { Switch }
