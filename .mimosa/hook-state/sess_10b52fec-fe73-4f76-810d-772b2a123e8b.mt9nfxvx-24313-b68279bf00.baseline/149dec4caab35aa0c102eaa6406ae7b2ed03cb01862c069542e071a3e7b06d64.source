export interface EnvVariable {
  key: string
  value: string
  masked_value: string
  category: string
  description: string
  required: boolean
  sensitive: boolean
  masked?: boolean
}

export interface EnvCategory {
  name: string
  variables: EnvVariable[]
  icon: string
  color: string
}

export interface Toast {
  id: string
  type: "success" | "error" | "info" | "warning"
  title: string
  message?: string
  duration?: number
}

export interface AuthState {
  isAuthenticated: boolean
  username?: string
  token?: string
}

export interface ApiResponse<T> {
  data: T
  error?: string
}
