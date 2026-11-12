import { useState } from "react"
import { toast } from "sonner"
import { KeyRound, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, ago, errText, usePoll, type Action, type Role, type User } from "@/lib/api"

const ROLE: Record<Role, string> = { owner: "Владелец", logist: "Логист" }
const ACTION: Record<string, string> = {
  load_reject: "отказ по грузу", rule_add: "добавил правило", rule_on: "включил правило", rule_off: "выключил правило",
  rule_delete: "удалил правило", dialog_taken: "взяли груз", dialog_declined: "отказ в диалоге", dialog_waiting: "вернул диалог",
  dialog_ready: "отметил «готово»", dialog_auto_on: "включил автоответ", dialog_auto_off: "выключил автоответ",
  message_send: "написал вручную", agent_start: "запустил агента", truck_free: "машина свободна", truck_busy: "машина занята",
  truck_add: "добавил машину", truck_edit: "изменил машину", settings: "изменил настройки", user_add: "добавил сотрудника",
  user_edit: "изменил сотрудника", call_now: "позвонить сейчас", dnc_add: "в стоп-лист",
}

export default function UsersPage({ me }: { me: User }) {
  const { data, reload } = usePoll<User[]>("/users", 15000)
  const { data: acts } = usePoll<Action[]>("/actions?limit=50", 15000)
  const [adding, setAdding] = useState(false)
  const [pwFor, setPwFor] = useState<User | null>(null)
  const patch = async (u: User, body: Partial<{ role: Role; active: boolean; password: string }>) => {
    try { await api(`/users/${u.id}`, { method: "PATCH", body }); toast.success("Сохранено"); await reload(); return true }
    catch (e) { toast.error(errText(e)); return false }
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Сотрудники</h1>
          <p className="text-sm text-muted-foreground">Логист видит грузы, переговоры, машины и правила. Владелец — ещё настройки и сотрудников.</p>
        </div>
        <Button onClick={() => setAdding(true)}><Plus />Добавить</Button>
      </div>
      <div className="overflow-hidden rounded-xl border bg-card">
        {data?.map((u) => (
          <div key={u.id} className="flex flex-wrap items-center gap-3 border-b p-4 last:border-b-0">
            <div className="min-w-32 flex-1">
              <div className="font-medium">{u.login}{u.id === me.id && <span className="ml-2 text-xs text-muted-foreground">это вы</span>}</div>
              {!u.active && <Badge variant="outline">отключён</Badge>}
            </div>
            <Select value={u.role} disabled={u.id === me.id} onValueChange={(v) => patch(u, { role: v as Role })}>
              <SelectTrigger className="w-36" aria-label={`Роль ${u.login}`}><SelectValue /></SelectTrigger>
              <SelectContent>{(Object.keys(ROLE) as Role[]).map((r) => <SelectItem key={r} value={r}>{ROLE[r]}</SelectItem>)}</SelectContent>
            </Select>
            <div className="flex items-center gap-2">
              <Switch id={`active-${u.id}`} checked={!!u.active} disabled={u.id === me.id} onCheckedChange={(v) => patch(u, { active: v })} />
              <Label htmlFor={`active-${u.id}`} className="text-sm">Доступ</Label>
            </div>
            <Button size="sm" variant="outline" onClick={() => setPwFor(u)}><KeyRound />Пароль</Button>
          </div>
        ))}
      </div>
      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Журнал действий</h2>
        <div className="flex flex-col gap-1 rounded-xl border bg-card p-3 text-sm">
          {!acts?.length && <div className="text-muted-foreground">Пока пусто</div>}
          {acts?.map((a) => (
            <div key={a.id} className="flex flex-wrap gap-x-2">
              <span className="text-muted-foreground">{ago(a.ts)}</span>
              <span className="font-medium">{a.login}</span>
              <span>{ACTION[a.action] ?? a.action}{a.ref ? ` #${a.ref}` : ""}</span>
              {a.detail && <span className="truncate text-muted-foreground">— {a.detail}</span>}
            </div>
          ))}
        </div>
      </div>
      <AddUser open={adding} onClose={() => setAdding(false)} onDone={reload} />
      <PasswordDialog u={pwFor} onClose={() => setPwFor(null)} onSave={(p) => pwFor ? patch(pwFor, { password: p }) : Promise.resolve(false)} />
    </div>
  )
}

function AddUser({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ login: "", password: "", role: "logist" as Role })
  const save = async () => {
    try { await api("/users", { body: f }); toast.success(`Сотрудник ${f.login} добавлен`); setF({ login: "", password: "", role: "logist" }); onClose(); onDone() }
    catch (e) { toast.error(errText(e)) }
  }
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Новый сотрудник</DialogTitle></DialogHeader>
        <div className="grid gap-3">
          <div className="flex flex-col gap-1.5"><Label htmlFor="u-login">Логин</Label><Input id="u-login" value={f.login} onChange={(e) => setF({ ...f, login: e.target.value })} /></div>
          <div className="flex flex-col gap-1.5"><Label htmlFor="u-pass">Пароль (от 8 символов)</Label><Input id="u-pass" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
          <div className="flex flex-col gap-1.5">
            <Label>Роль</Label>
            <Select value={f.role} onValueChange={(v) => setF({ ...f, role: v as Role })}>
              <SelectTrigger aria-label="Роль"><SelectValue /></SelectTrigger>
              <SelectContent>{(Object.keys(ROLE) as Role[]).map((r) => <SelectItem key={r} value={r}>{ROLE[r]}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Отмена</Button><Button onClick={save}>Добавить</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PasswordDialog({ u, onClose, onSave }: { u: User | null; onClose: () => void; onSave: (p: string) => Promise<boolean> }) {
  const [p, setP] = useState("")
  return (
    <Dialog open={!!u} onOpenChange={(o) => { if (!o) { setP(""); onClose() } }}>
      <DialogContent>
        <DialogHeader><DialogTitle>Новый пароль для {u?.login}</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-1.5"><Label htmlFor="pw">Пароль (от 8 символов)</Label><Input id="pw" type="password" autoComplete="new-password" value={p} onChange={(e) => setP(e.target.value)} /></div>
        <p className="text-xs text-muted-foreground">Сотрудника выкинет из системы — войдёт с новым паролем.</p>
        <DialogFooter><Button variant="outline" onClick={onClose}>Отмена</Button><Button disabled={p.length < 8} onClick={async () => { if (p.length >= 8 && await onSave(p)) { setP(""); onClose() } }}>Сохранить</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
