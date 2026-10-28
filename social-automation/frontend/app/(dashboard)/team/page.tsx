'use client'

import { useState, useEffect, useCallback } from 'react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { useAuth } from '@/hooks/useAuth'
import { teamsApi, authApi } from '@/services/api'
import { setTokens } from '@/services/api'
import toast from 'react-hot-toast'
import { Users, Trash2, UserPlus, Crown, Shield, Edit, Eye, ChevronRight } from 'lucide-react'

interface Team {
  id: string
  name: string
  owner_id: string
  member_count: number
  role: string
}

interface Member {
  user_id: string
  email: string
  name: string | null
  role: string
}

interface TeamDetail {
  id: string
  name: string
  owner_id: string
  members: Member[]
}

const roleIcon: Record<string, typeof Crown> = {
  owner: Crown,
  admin: Shield,
  editor: Edit,
  viewer: Eye,
}

export default function TeamPage() {
  const { user, refreshUser } = useAuth()
  const [teams, setTeams] = useState<Team[]>([])
  const [activeTeam, setActiveTeam] = useState<TeamDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('editor')
  const [isInviting, setIsInviting] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')
  const [isCreating, setIsCreating] = useState(false)

  const loadTeams = useCallback(async () => {
    try {
      const res = await teamsApi.list()
      setTeams(res.data)
      if (res.data.length > 0 && !activeTeam) {
        const detail = await teamsApi.get(res.data[0].id)
        setActiveTeam(detail.data)
      }
    } catch {
      toast.error('Failed to load teams')
    } finally {
      setIsLoading(false)
    }
  }, [activeTeam])

  useEffect(() => {
    loadTeams()
  }, [loadTeams])

  const loadTeamDetail = async (teamId: string) => {
    try {
      const res = await teamsApi.get(teamId)
      setActiveTeam(res.data)
    } catch {
      toast.error('Failed to load team details')
    }
  }

  const handleSwitchTeam = async (teamId: string) => {
    try {
      const res = await authApi.switchTeam(teamId)
      setTokens(res.data.access_token, res.data.refresh_token)
      await refreshUser()
      await loadTeamDetail(teamId)
      toast.success('Switched team')
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to switch team')
    }
  }

  const handleCreateTeam = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTeamName.trim()) return
    setIsCreating(true)
    try {
      await teamsApi.create(newTeamName)
      setNewTeamName('')
      toast.success('Team created')
      await loadTeams()
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to create team')
    } finally {
      setIsCreating(false)
    }
  }

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inviteEmail.trim() || !activeTeam) return
    setIsInviting(true)
    try {
      await teamsApi.invite(activeTeam.id, inviteEmail, inviteRole)
      setInviteEmail('')
      toast.success('Invitation sent')
      await loadTeamDetail(activeTeam.id)
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to send invite')
    } finally {
      setIsInviting(false)
    }
  }

  const handleRemoveMember = async (userId: string) => {
    if (!activeTeam) return
    if (userId === user?.id) {
      toast.error('You cannot remove yourself. Ask another admin.')
      return
    }
    try {
      await teamsApi.removeMember(activeTeam.id, userId)
      toast.success('Member removed')
      await loadTeamDetail(activeTeam.id)
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to remove member')
    }
  }

  const handleChangeRole = async (userId: string, newRole: string) => {
    if (!activeTeam) return
    try {
      await teamsApi.changeRole(activeTeam.id, userId, newRole)
      toast.success('Role updated')
      await loadTeamDetail(activeTeam.id)
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { detail?: string } } }
      toast.error(axiosError.response?.data?.detail || 'Failed to update role')
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <p className="text-muted-foreground">Loading teams...</p>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold">Team Management</h1>
        <p className="text-muted-foreground">Manage your teams, invite members, and switch between teams.</p>
      </div>

      {/* Team list + create */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Users className="h-5 w-5" /> Your Teams</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {teams.length === 0 && (
              <p className="text-sm text-muted-foreground">No teams yet. Create one below.</p>
            )}
            {teams.map(team => (
              <div
                key={team.id}
                className={`flex items-center justify-between rounded-lg border p-3 cursor-pointer hover:bg-muted/50 transition-colors ${activeTeam?.id === team.id ? 'border-primary bg-muted/30' : ''}`}
                onClick={() => loadTeamDetail(team.id)}
              >
                <div className="flex items-center gap-3">
                  <div>
                    <p className="font-medium">{team.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {team.member_count} member{team.member_count !== 1 ? 's' : ''} · You are {team.role}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {activeTeam?.id !== team.id && (
                    <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); handleSwitchTeam(team.id) }}>
                      Switch <ChevronRight className="ml-1 h-4 w-4" />
                    </Button>
                  )}
                  {activeTeam?.id === team.id && (
                    <span className="text-sm text-primary font-medium">Active</span>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Create New Team</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreateTeam} className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="teamName">Team Name</Label>
                <Input id="teamName" value={newTeamName} onChange={(e) => setNewTeamName(e.target.value)}
                  placeholder="My Team" disabled={isCreating} />
              </div>
              <Button type="submit" className="w-full" isLoading={isCreating} disabled={!newTeamName.trim()}>
                Create Team
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>

      {/* Active team details */}
      {activeTeam && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Members of {activeTeam.name}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {activeTeam.members.map(member => {
                const Icon = roleIcon[member.role] || Eye
                return (
                  <div key={member.user_id} className="flex items-center justify-between rounded-lg border p-3">
                    <div className="flex items-center gap-3">
                      <Icon className="h-4 w-4 text-muted-foreground" />
                      <div>
                        <p className="font-medium">{member.name || member.email}</p>
                        <p className="text-sm text-muted-foreground">{member.email}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {member.user_id !== activeTeam.owner_id && (
                        <select
                          className="rounded border bg-background px-2 py-1 text-sm"
                          value={member.role}
                          onChange={(e) => handleChangeRole(member.user_id, e.target.value)}
                          disabled={member.user_id === user?.id}
                        >
                          <option value="admin">Admin</option>
                          <option value="editor">Editor</option>
                          <option value="viewer">Viewer</option>
                        </select>
                      )}
                      {member.role === 'owner' && (
                        <span className="text-sm font-medium text-primary">Owner</span>
                      )}
                      {member.user_id !== activeTeam.owner_id && member.user_id !== user?.id && (
                        <Button size="sm" variant="ghost" onClick={() => handleRemoveMember(member.user_id)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><UserPlus className="h-5 w-5" /> Invite Member</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleInvite} className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="inviteEmail">Email Address</Label>
                  <Input id="inviteEmail" type="email" value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="colleague@example.com" disabled={isInviting} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="inviteRole">Role</Label>
                  <select id="inviteRole" className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} disabled={isInviting}>
                    <option value="admin">Admin — can manage members and settings</option>
                    <option value="editor">Editor — can create and publish content</option>
                    <option value="viewer">Viewer — read-only access</option>
                  </select>
                </div>
                <Button type="submit" className="w-full" isLoading={isInviting} disabled={!inviteEmail.trim()}>
                  Send Invitation
                </Button>
                <p className="text-xs text-muted-foreground">
                  If the person already has an account, they will be added immediately.
                  Otherwise, they will receive an email with a link to register and join.
                </p>
              </form>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
