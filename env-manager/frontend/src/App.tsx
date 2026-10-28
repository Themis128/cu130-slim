import { useState, useEffect, useCallback } from "react"
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { Toaster } from "@/components/ui/Toaster"
import { Sidebar } from "@/components/layout/Sidebar"
import { Header } from "@/components/layout/Header"
import { EnvTable } from "@/components/env/EnvTable"
import { LoginPage } from "@/components/auth/LoginPage"
import { EnvCategory, EnvVariable } from "@/types/env"
import { useToasts } from "@/hooks/use-toasts"
import { cn } from "@/lib/utils"
import { Loader2, Save, Eye, EyeOff } from "lucide-react"
import { Button } from "@/components/ui/Button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/Dialog"

const API_BASE = "/api"

function useAuth() {
  const [auth, setAuth] = useState<{ user: string; token: string } | null>(() => {
    const stored = localStorage.getItem("env_manager_auth")
    return stored ? JSON.parse(stored) : null
  })
  const [loading, setLoading] = useState(true)

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch(`${API_BASE}/env`, {
      headers: {
        "Authorization": `Basic ${btoa(`${username}:${password}`)}`
      }
    })
    if (!res.ok) throw new Error("Invalid credentials")
    const token = btoa(`${username}:${password}`)
    const newAuth = { user: username, token }
    setAuth(newAuth)
    localStorage.setItem("env_manager_auth", JSON.stringify(newAuth))
    return true
  }, [])

  const logout = useCallback(() => {
    setAuth(null)
    localStorage.removeItem("env_manager_auth")
  }, [])

  useEffect(() => {
    setLoading(false)
  }, [])

  return { auth, login, logout, loading }
}

function useEnvData(auth: { user: string; token: string } | null) {
  const [categories, setCategories] = useState<EnvCategory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchEnv = useCallback(async () => {
    if (!auth) return
    try {
      setLoading(true)
      const res = await fetch(`${API_BASE}/env`, {
        headers: { "Authorization": `Basic ${auth.token}` }
      })
      if (!res.ok) throw new Error("Failed to fetch environment variables")
      const data = await res.json() as EnvVariable[]
      
      // Group variables by category
      const grouped = data.reduce<Record<string, EnvVariable[]>>((acc, variable) => {
        if (!acc[variable.category]) {
          acc[variable.category] = []
        }
        acc[variable.category].push(variable)
        return acc
      }, {})
      
      // Convert to EnvCategory[] format
      const categoryIcons: Record<string, string> = {
        "Social Media": "Zap",
        "AI Services": "Cloud",
        "Core": "Shield",
        "Database": "Database",
        "Storage": "Key",
        "Settings": "Settings",
      }
      const categoryColors: Record<string, string> = {
        "Social Media": "text-blue-500",
        "AI Services": "text-purple-500",
        "Core": "text-green-500",
        "Database": "text-orange-500",
        "Storage": "text-red-500",
        "Settings": "text-gray-500",
      }
      const categorized: EnvCategory[] = Object.keys(grouped).map(categoryName => ({
        name: categoryName,
        variables: grouped[categoryName],
        icon: categoryIcons[categoryName] || "Key",
        color: categoryColors[categoryName] || "text-gray-500",
      }))
      
      setCategories(categorized)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setLoading(false)
    }
  }, [auth])

  const saveEnv = useCallback(async (variables: EnvVariable[]) => {
    if (!auth) return
    try {
      const updates: Record<string, string> = {}
      variables.forEach(v => {
        updates[v.key] = v.value
      })
      const res = await fetch(`${API_BASE}/env`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Basic ${auth.token}`
        },
        body: JSON.stringify({ updates })
      })
      if (!res.ok) throw new Error("Failed to save environment variables")
      await fetchEnv()
      return true
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : "Failed to save")
    }
  }, [auth, fetchEnv])

  useEffect(() => {
    if (auth) fetchEnv()
  }, [auth, fetchEnv])

  return { categories, setCategories, loading, error, fetchEnv, saveEnv }
}

function Dashboard() {
  const { auth, logout } = useAuth()
  const { categories, setCategories, loading, error, saveEnv } = useEnvData(auth)
  const { toasts, addToast, removeToast } = useToasts()
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [showAllValues, setShowAllValues] = useState(false)
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const stored = localStorage.getItem("env_manager_theme")
    return (stored as "light" | "dark") || "light"
  })

  // Apply theme to document
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    localStorage.setItem("env_manager_theme", theme)
  }, [theme])

  const handleThemeChange = useCallback((newTheme: "light" | "dark") => {
    setTheme(newTheme)
  }, [])

  // Initialize active category
  useEffect(() => {
    if (categories.length > 0 && !activeCategory) {
      setActiveCategory(categories[0].name)
    }
  }, [categories, activeCategory])

  const currentCategory = categories.find(c => c.name === activeCategory)
  const currentVariables = currentCategory?.variables || []

  const handleVariableChange = useCallback((key: string, value: string) => {
    setCategories(prev => prev.map(cat => ({
      ...cat,
      variables: cat.variables.map(v => 
        v.key === key ? { ...v, value } : v
      )
    })))
    setDirty(true)
  }, [setCategories])

  const handleMaskToggle = useCallback((key: string) => {
    setCategories(prev => prev.map(cat => ({
      ...cat,
      variables: cat.variables.map(v => 
        v.key === key ? { ...v, masked: !v.masked } : v
      )
    })))
  }, [setCategories])

  const handleSave = useCallback(async () => {
    try {
      const allVariables = categories.flatMap(c => c.variables)
      await saveEnv(allVariables)
      setDirty(false)
      setSaveDialogOpen(false)
      addToast({ type: "success", title: "Saved", message: "Environment variables saved successfully" })
    } catch (err) {
      addToast({ type: "error", title: "Error", message: err instanceof Error ? err.message : "Failed to save" })
    }
  }, [categories, saveEnv, addToast])

  const handleCancel = () => {
    setSaveDialogOpen(false)
  }

  const handleDiscard = () => {
    // Reload from server
    window.location.reload()
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="text-center p-8">
          <div className="text-destructive mb-4">⚠️</div>
          <h2 className="text-xl font-semibold mb-2">Failed to load environment</h2>
          <p className="text-muted-foreground mb-4">{error}</p>
          <Button onClick={() => window.location.reload()}>Retry</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <Sidebar
        collapsed={!sidebarOpen}
        onToggleCollapse={() => setSidebarOpen(!sidebarOpen)}
        categories={categories}
        activeCategory={activeCategory || (categories[0]?.name || "")}
        onCategoryChange={setActiveCategory}
      />

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Header */}
        <Header
          onMenuClick={() => setSidebarOpen(!sidebarOpen)}
          sidebarCollapsed={false}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          theme={theme}
          onThemeChange={handleThemeChange}
          user={{ name: auth?.user || "Admin", email: "admin@example.com" }}
          onLogout={logout}
          notificationCount={toasts.length}
        />

        {/* Page Content */}
        <main className="flex-1 overflow-auto p-6 lg:p-8">
          <div className="max-w-7xl mx-auto">
            {/* Category Header */}
            {currentCategory && (
              <div className="mb-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h1 className="text-3xl font-bold tracking-tight text-foreground">{currentCategory.name}</h1>
                    <p className="text-muted-foreground mt-1">
                      {currentCategory.variables.length} variables • {currentCategory.variables.filter(v => v.required).length} required
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setShowAllValues(!showAllValues)}
                      className={cn(showAllValues && "bg-primary text-primary-foreground")}
                    >
                      {showAllValues ? <EyeOff className="h-4 w-4 mr-2" /> : <Eye className="h-4 w-4 mr-2" />}
                      {showAllValues ? "Hide Values" : "Show Values"}
                    </Button>
                    <Button
                      variant={dirty ? "default" : "outline"}
                      disabled={!dirty}
                      onClick={() => setSaveDialogOpen(true)}
                    >
                      <Save className="h-4 w-4 mr-2" />
                      Save Changes
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {/* Env Table */}
            {currentCategory && (
              <EnvTable
                variables={currentVariables}
                onVariableChange={handleVariableChange}
                onMaskToggle={handleMaskToggle}
                showValues={showAllValues}
                dirty={dirty}
              />
            )}

            {/* Empty State */}
            {!currentCategory && (
              <div className="flex flex-col items-center justify-center h-[400px] text-center">
                <div className="text-6xl mb-4">🔧</div>
                <h2 className="text-2xl font-semibold mb-2">No category selected</h2>
                <p className="text-muted-foreground">Select a category from the sidebar to manage its environment variables</p>
              </div>
            )}
          </div>
        </main>
      </div>

      {/* Save Dialog */}
      <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Save Changes?</DialogTitle>
            <DialogDescription>
              You have unsaved changes. Would you like to save them to the .env file?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={handleDiscard}>Discard</Button>
            <Button variant="outline" onClick={handleCancel}>Cancel</Button>
            <Button onClick={handleSave} disabled={!dirty}>
              <Save className="h-4 w-4 mr-2" />
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Toasts */}
      <Toaster toasts={toasts} onRemove={removeToast} />
    </div>
  )
}

function AppRoutes() {
  const { auth, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/*" element={auth ? <Dashboard /> : <Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  return (
    <AppRoutes />
  )
}