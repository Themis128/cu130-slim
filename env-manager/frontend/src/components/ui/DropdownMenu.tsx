import * as React from "react"
import { cn } from "@/lib/utils"
import { ChevronDown, Check, Circle } from "lucide-react"

interface DropdownMenuProps {
  children: React.ReactNode
  trigger: React.ReactNode
  align?: "start" | "end"
}

const DropdownMenu: React.FC<DropdownMenuProps> = ({ children, trigger, align = "end" }) => {
  const [isOpen, setIsOpen] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  return (
    <div ref={ref} className="relative inline-block" tabIndex={0}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setIsOpen(!isOpen); }}}
        className="inline-flex items-center"
        tabIndex={0}
        role="button"
        aria-haspopup="true"
        aria-expanded={isOpen}
      >
        {trigger}
      </div>
      {isOpen && (
        <div
          className={cn(
            "absolute z-50 mt-2 min-w-[8rem] origin-top-right rounded-md border bg-popover p-1 text-popover-foreground shadow-lg animate-fade-in",
            align === "end" ? "right-0" : "left-0"
          )}
          role="menu"
        >
          {React.Children.map(children, (child) => {
            if (!React.isValidElement(child)) return child
            return React.cloneElement(child as React.ReactElement<any>, { onSelect: () => setIsOpen(false) })
          })}
        </div>
      )}
    </div>
  )
}

interface DropdownMenuItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  onSelect?: () => void
  inset?: boolean
  disabled?: boolean
  shortcut?: string
}

const DropdownMenuItem = React.forwardRef<HTMLButtonElement, DropdownMenuItemProps>(
  ({ className, onSelect, inset, disabled, shortcut, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "relative flex w-full cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none transition-colors focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        inset && "pl-8",
        className
      )}
      onClick={(e) => {
        if (!disabled) {
          onSelect?.()
          props.onClick?.(e)
        }
      }}
      disabled={disabled}
      role="menuitem"
      tabIndex={-1}
      {...props}
    >
      {children}
      {shortcut && (
        <span className="ml-auto text-xs tracking-widest text-muted-foreground">{shortcut}</span>
      )}
    </button>
  )
)
DropdownMenuItem.displayName = "DropdownMenuItem"

interface DropdownMenuSeparatorProps extends React.HTMLAttributes<HTMLDivElement> {}

const DropdownMenuSeparator = React.forwardRef<HTMLDivElement, DropdownMenuSeparatorProps>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      role="separator"
      {...props}
    />
  )
)
DropdownMenuSeparator.displayName = "DropdownMenuSeparator"

interface DropdownMenuLabelProps extends React.HTMLAttributes<HTMLDivElement> {
  inset?: boolean
}

const DropdownMenuLabel = React.forwardRef<HTMLDivElement, DropdownMenuLabelProps>(
  ({ className, inset, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("px-2 py-1.5 text-sm font-semibold text-muted-foreground", inset && "pl-8", className)}
      {...props}
    />
  )
)
DropdownMenuLabel.displayName = "DropdownMenuLabel"

interface DropdownMenuCheckboxItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  checked?: boolean
  onSelect?: () => void
}

const DropdownMenuCheckboxItem = React.forwardRef<HTMLButtonElement, DropdownMenuCheckboxItemProps>(
  ({ className, checked, onSelect, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-sm outline-none transition-colors focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className
      )}
      onClick={(e) => {
        onSelect?.()
        props.onClick?.(e)
      }}
      role="menuitemcheckbox"
      aria-checked={checked}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        {checked && <Check className="h-4 w-4" />}
      </span>
      {children}
    </button>
  )
)
DropdownMenuCheckboxItem.displayName = "DropdownMenuCheckboxItem"

export { DropdownMenu, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuLabel, DropdownMenuCheckboxItem }
