import * as React from "react"
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Toast } from "@/types/env"

interface ToasterProps {
  toasts: Toast[]
  onRemove: (id: string) => void
}

export const Toaster: React.FC<ToasterProps> = ({ toasts, onRemove }) => {
  const icons = {
    success: <CheckCircle className="h-5 w-5 text-green-500" />,
    error: <AlertCircle className="h-5 w-5 text-red-500" />,
    warning: <AlertTriangle className="h-5 w-5 text-yellow-500" />,
    info: <Info className="h-5 w-5 text-blue-500" />,
  }

  const bgColors = {
    success: "bg-green-50 border-green-200 dark:bg-green-900/20 dark:border-green-800",
    error: "bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-800",
    warning: "bg-yellow-50 border-yellow-200 dark:bg-yellow-900/20 dark:border-yellow-800",
    info: "bg-blue-50 border-blue-200 dark:bg-blue-900/20 dark:border-blue-800",
  }

  const textColors = {
    success: "text-green-800 dark:text-green-200",
    error: "text-red-800 dark:text-red-200",
    warning: "text-yellow-800 dark:text-yellow-200",
    info: "text-blue-800 dark:text-blue-200",
  }

  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-full"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={cn(
            "relative flex items-start gap-3 p-4 rounded-xl border shadow-lg animate-slide-in",
            bgColors[toast.type],
            textColors[toast.type]
          )}
          role="alert"
        >
          <div className="flex-shrink-0 mt-0.5">{icons[toast.type]}</div>
          <div className="flex-1 min-w-0">
            <p className="font-medium">{toast.title}</p>
            {toast.message && (
              <p className="text-sm opacity-90 mt-0.5">{toast.message}</p>
            )}
          </div>
          <button
            onClick={() => onRemove(toast.id)}
            className="flex-shrink-0 p-1 rounded-lg hover:bg-black/10 dark:hover:bg-white/10 transition-colors"
            aria-label="Dismiss notification"
          >
            <X className="h-4 w-4 opacity-60 hover:opacity-100" />
          </button>
        </div>
      ))}
    </div>
  )
}

export const useToasts = () => {
  const [toasts, setToasts] = React.useState<Toast[]>([])

  const addToast = React.useCallback((toast: Omit<Toast, "id">) => {
    const id = Math.random().toString(36).substr(2, 9)
    setToasts(prev => [...prev, { ...toast, id }])
    if (toast.duration !== 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id))
      }, toast.duration || 5000)
    }
    return id
  }, [])

  const removeToast = React.useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return { toasts, addToast, removeToast }
}
