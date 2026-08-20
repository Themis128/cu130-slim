import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Loader2, Lock, Unlock, Eye, EyeOff, Shield, AlertCircle, CheckCircle, LogIn } from "lucide-react"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Input"
import { Label } from "@/components/ui/Label"
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/Card"
import { Separator } from "@/components/ui/Separator"
import { cn } from "@/lib/utils"

export const LoginPage: React.FC = () => {
  const navigate = useNavigate()
  const [username, setUsername] = useState("admin")
  const [password, setPassword] = useState("admin")
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const res = await fetch("/api/env", {
        headers: {
          "Authorization": `Basic ${btoa(`${username}:${password}`)}`
        }
      })

      if (!res.ok) {
        if (res.status === 401) {
          throw new Error("Invalid username or password")
        }
        throw new Error("Login failed. Please try again.")
      }

      const token = btoa(`${username}:${password}`)
      const auth = { user: username, token }
      localStorage.setItem("env_manager_auth", JSON.stringify(auth))
      
      navigate("/")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary/50 to-background p-4">
      <div className="w-full max-w-md">
        {/* Logo/Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-primary/20 mb-6">
            <Shield className="h-10 w-10 text-primary" />
          </div>
          <h1 className="text-4xl font-bold text-foreground mb-2">Env Manager</h1>
          <p className="text-muted-foreground mb-4">Securely manage your environment variables</p>
        </div>

        {/* Login Card */}
        <Card className="shadow-2xl bg-card/80 backdrop-blur-sm border border-border/50">
          <CardHeader className="text-center pb-6">
            <CardTitle className="text-2xl font-bold text-foreground">Sign in to your account</CardTitle>
            <CardDescription className="text-muted-foreground">
              Enter your credentials to access the dashboard
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <div className="flex items-center gap-3 p-4 rounded-lg bg-destructive/50 text-destructive">
                  <AlertCircle className="h-5 w-5" />
                  <div>
                    <p className="text-sm font-medium">{error}</p>
                  </div>
                </div>
              )}

              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="username">Username</Label>
                  <div className="relative">
                    <Input
                      id="username"
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="Enter username"
                      disabled={loading}
                      required
                      autoComplete="username"
                      className="pl-11 pr-3"
                    />
                    <Unlock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground/70" />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <div className="relative">
                    <Input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter password"
                      disabled={loading}
                      required
                      autoComplete="current-password"
                      className="pl-11 pr-11"
                    />
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground/70" />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/70 hover:text-muted-foreground"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <div className="flex items-start">
                  <div className="flex items-center h-6">
                    <input id="remember-me" type="checkbox" className="rounded border-input text-primary h-4 w-4" />
                    <label htmlFor="remember-me" className="ml-2 text-sm text-muted-foreground">
                      Remember me
                    </label>
                  </div>
                </div>
              </div>

              <Button type="submit" className="w-full py-3 text-lg font-medium disabled={loading}">
                {loading ? (
                  <>
                    <Loader2 className="mr-3 h-5 w-5 animate-spin" />
                    Signing in...
                  </>
                ) : (
                  <>
                    <CheckCircle className="mr-3 h-4 w-4" />
                    Sign in
                  </>
                )}
              </Button>
            </form>
          </CardContent>
          {/* Footer */}
          <CardFooter className="border-t pt-6">
            <Separator className="my-6" />
            <div className="space-y-4 text-center">
              <p className="text-sm text-muted-foreground">
                Default credentials: <code className="bg-muted px-2 py-0.5 rounded">admin</code> / <code className="bg-muted px-2 py-0.5 rounded">admin</code>
              </p>
              <p className="text-xs text-muted-foreground">
                Change these in the .env file after first login
              </p>
              <div className="flex items-center justify-center space-x-3 mt-2">
                <a href="#" className="text-sm text-primary hover:text-primary/80 transition-colors">
                  Forgot password?
                </a>
                <a href="#" className="text-sm text-muted-foreground hover:text-muted-foreground/80 transition-colors">
                  Need help?
                </a>
              </div>
            </div>
          </CardFooter>
        </Card>
      </div>
    </div>
  )
}
