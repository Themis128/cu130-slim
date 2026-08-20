import * as React from "react"
import { cn } from "@/lib/utils"

export interface TooltipProps {
  content: React.ReactNode
  children: React.ReactElement
  side?: "top" | "right" | "bottom" | "left"
  delayDuration?: number
  sideOffset?: number
  className?: string
}

const Tooltip = React.forwardRef<HTMLDivElement, TooltipProps>(
  ({ content, children, side = "top", delayDuration = 200, sideOffset = 4, className, ...props }, ref) => {
    const [isOpen, setIsOpen] = React.useState(false)
    const timeoutRef = React.useRef<ReturnType<typeof setTimeout>>()
    const childRef = React.useRef<HTMLElement>(null)

    const showTooltip = () => {
      timeoutRef.current = setTimeout(() => setIsOpen(true), delayDuration)
    }

    const hideTooltip = () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      setIsOpen(false)
    }

    const sideStyles = {
      top: "bottom-full left-1/2 -translate-x-1/2 mb-2",
      right: "left-full top-1/2 -translate-y-1/2 ml-2",
      bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
      left: "right-full top-1/2 -translate-y-1/2 mr-2",
    }

    const arrowStyles = {
      top: "top-full left-1/2 -translate-x-1/2 border-t-border",
      right: "right-full top-1/2 -translate-y-1/2 border-l-border",
      bottom: "bottom-full left-1/2 -translate-x-1/2 border-b-border",
      left: "left-full top-1/2 -translate-y-1/2 border-r-border",
    }

    if (!React.isValidElement(children)) {
      return <>{children}</>
    }

    const childProps = children.props as React.HTMLAttributes<HTMLElement>

    return (
      <div ref={ref} className={cn("relative inline-block", className)} {...props}>
        {React.cloneElement(children as React.ReactElement<any>, {
          ref: childRef,
          onMouseEnter: (e: React.MouseEvent<HTMLElement>) => { showTooltip(); childProps.onMouseEnter?.(e) },
          onMouseLeave: (e: React.MouseEvent<HTMLElement>) => { hideTooltip(); childProps.onMouseLeave?.(e) },
          onFocus: (e: React.FocusEvent<HTMLElement>) => { showTooltip(); childProps.onFocus?.(e) },
          onBlur: (e: React.FocusEvent<HTMLElement>) => { hideTooltip(); childProps.onBlur?.(e) },
        })}
        {isOpen && (
          <div
            className={cn(
              "absolute z-50 px-3 py-1.5 text-xs font-medium text-popover-foreground bg-popover border rounded-md shadow-lg animate-fade-in",
              sideStyles[side]
            )}
            role="tooltip"
          >
            {content}
            <div
              className={cn(
                "absolute w-0 h-0 border-4 border-transparent",
                arrowStyles[side]
              )}
            />
          </div>
        )}
      </div>
    )
  }
)
Tooltip.displayName = "Tooltip"

export { Tooltip }