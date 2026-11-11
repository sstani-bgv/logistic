import { useState } from "react"
import { Loader2, Radar } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, errText, type User } from "@/lib/api"

export default function LoginPage({ onLogin }: { onLogin: (u: User) => void }) {
  const [login, setLogin] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError("")
    try { onLogin(await api<User>("/login", { body: { login, password } })) }
    catch (err) { setError(errText(err)) } finally { setBusy(false) }
  }
  return (
    <div className="grid min-h-screen place-items-center bg-background px-4">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground"><Radar className="size-5" /></div>
          <div className="leading-tight">
            <div className="font-semibold">Диспетчер</div>
            <div className="text-xs text-muted-foreground">Вход для сотрудников</div>
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="login">Логин</Label>
          <Input id="login" autoComplete="username" autoFocus value={login} onChange={(e) => setLogin(e.target.value)} required />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Пароль</Label>
          <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <div role="alert" className="text-sm text-destructive">{error}</div>}
        <Button type="submit" disabled={busy}>{busy && <Loader2 className="animate-spin" />}Войти</Button>
      </form>
    </div>
  )
}
