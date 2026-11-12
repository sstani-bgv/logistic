import { useEffect, useState } from "react"
import { BookOpenCheck, MessagesSquare, PackageSearch, Truck as TruckIcon, Radar, Clock, Filter, Sparkles, Settings as SettingsIcon, Users as UsersIcon, LogOut } from "lucide-react"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import { api, usePoll, ago, AUTH_EVENT, type Stats, type User } from "@/lib/api"
import { cn } from "@/lib/utils"
import LoadsPage from "@/pages/Loads"
import DialogsPage from "@/pages/Dialogs"
import TrucksPage from "@/pages/Trucks"
import KnowledgePage from "@/pages/Knowledge"
import LoginPage from "@/pages/Login"
import SettingsPage from "@/pages/Settings"
import UsersPage from "@/pages/Users"

const NAV = [
  { id: "loads", label: "Грузы", icon: PackageSearch },
  { id: "dialogs", label: "Переговоры", icon: MessagesSquare },
  { id: "trucks", label: "Машины", icon: TruckIcon },
  { id: "knowledge", label: "База знаний", icon: BookOpenCheck },
  { id: "settings", label: "Настройки", icon: SettingsIcon, owner: true },
  { id: "users", label: "Сотрудники", icon: UsersIcon, owner: true },
] as const
type Page = (typeof NAV)[number]["id"]

export default function App() {
  const [me, setMe] = useState<User | null | undefined>(undefined)
  useEffect(() => {
    api<User>("/me").then(setMe).catch(() => setMe(null))
    const out = () => setMe(null)
    window.addEventListener(AUTH_EVENT, out)
    return () => window.removeEventListener(AUTH_EVENT, out)
  }, [])
  if (me === undefined) return <div role="status" className="grid min-h-svh place-items-center text-sm text-muted-foreground">Загрузка…</div>
  if (!me) return <><LoginPage onLogin={setMe} /><Toaster position="bottom-right" /></>
  const logout = async () => { await api("/logout", { method: "POST" }).catch(() => {}); setMe(null) }
  return <Shell me={me} onLogout={logout} />
}

const PART: Record<string, string> = { ati: "АТИ", llm: "Модель", phone: "Звонки", backup: "Бэкап", agent: "Агент" }

function Shell({ me, onLogout }: { me: User; onLogout: () => void }) {
  const nav = NAV.filter((n) => !("owner" in n) || me.role === "owner")
  const [page, setPage] = useState<Page>(() => (nav.find((n) => n.id === location.hash.slice(1))?.id ?? "loads"))
  useEffect(() => { location.hash = page }, [page])
  const { data: s, offline } = usePoll<Stats>("/stats", 4000)
  const { data: st } = usePoll<{ send_enabled?: boolean; auto_start?: boolean; call_enabled?: boolean }>("/settings", 30000)
  const agentOff = !!st && !st.send_enabled && !st.auto_start
  const problems = s?.health?.problems ?? []
  const errors = problems.filter((p) => p.level === "error")
  const atiBad = s?.health?.ati_ok === false || errors.some((p) => p.part === "ati")

  const counters: Record<string, number | undefined> = { loads: s?.relevant, dialogs: s ? s.dialogs + s.ready : undefined, knowledge: s?.rules }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-screen bg-background text-foreground">
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card px-3 py-5 md:flex">
          <div className="flex items-center gap-2.5 px-2 pb-6">
            <div className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground"><Radar className="size-5" /></div>
            <div className="leading-tight">
              <div className="font-semibold">Диспетчер</div>
              <div className="text-xs text-muted-foreground">ИИ-ассистент логиста</div>
            </div>
          </div>
          <nav className="flex flex-col gap-1">
            {nav.map((n) => (
              <button key={n.id} onClick={() => setPage(n.id)}
                className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  page === n.id ? "bg-accent font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                <n.icon className="size-4" />
                <span className="flex-1 text-left">{n.label}</span>
                {!!counters[n.id] && <span className="num rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{counters[n.id]}</span>}
              </button>
            ))}
          </nav>
          <div className={cn("mt-auto rounded-lg border p-3 text-xs", atiBad ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40" : "bg-muted/40")} role="status">
            <div className="mb-1 flex items-center gap-1.5 font-medium">
              <span className={cn("size-2 rounded-full", offline ? "bg-amber-500" : atiBad ? "bg-red-500" : s?.health?.ati_ok ? "animate-pulse bg-emerald-500" : "bg-muted-foreground")} />
              {offline ? "Нет связи с сервером" : atiBad ? "АТИ: ошибка" : s?.health?.ati_ok ? "Мониторинг АТИ работает" : "Подключаемся к АТИ…"}
            </div>
            <div className="text-muted-foreground">
              {atiBad ? errors.find((p) => p.part === "ati")?.text : s?.me?.name ? `Аккаунт: ${s.me.name}` : "Проверяем токен и тариф"}
              {!atiBad && !!s?.health?.ati_last_scan && <> · проход {ago(s.health.ati_last_scan)}</>}
            </div>
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 px-1 text-sm">
            <div className="min-w-0 truncate"><span className="font-medium">{me.login}</span> <span className="text-xs text-muted-foreground">{me.role === "owner" ? "владелец" : "логист"}</span></div>
            <button onClick={onLogout} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Выйти" title="Выйти"><LogOut className="size-4" /></button>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b bg-card">
            <div className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
              <Stat icon={Radar} label="Просмотрено ботом сегодня" value={s?.views} hint={`${s?.scans ?? 0} проходов по бирже`} />
              <Stat icon={Filter} label="Отсеяно автоматически" value={s?.filtered} hint="не подходят по правилам" />
              <Stat icon={Sparkles} label="Подходящих сейчас" value={s?.relevant} hint={`${s?.ready ?? 0} готовы к звонку`} accent />
              <Stat icon={Clock} label="Рутины снято с логиста" value={s ? `${s.hours_saved} ч` : undefined} hint="≈25 сек на просмотр груза" />
            </div>
          </header>
          <div className="flex gap-6 px-4 py-6 md:px-8">
            <div className="min-w-0 flex-1">
              {offline && (
                <div role="alert" className="mb-5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
                  <b>Нет связи с сервером.</b> На экране последние полученные данные — они могут быть устаревшими.
                </div>
              )}
              {problems.filter((p) => p.level !== "info").length > 0 && (
                <div role="alert" className="mb-5 flex flex-col gap-1 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-950 dark:border-red-900 dark:bg-red-950/40 dark:text-red-100">
                  <b>Требует внимания</b>
                  {problems.filter((p) => p.level !== "info").map((p, i) => (
                    <div key={i} className="flex gap-2"><span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", p.level === "error" ? "bg-red-500" : "bg-amber-500")} />{PART[p.part]}: {p.text}</div>
                  ))}
                </div>
              )}
              {agentOff && (
                <div role="status" className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-100">
                  <span className="min-w-0 flex-1"><b>Агент пока выключен.</b> Ассистент ищет и оценивает грузы, но никому не пишет{st?.call_enabled ? "" : " и не звонит"} — можно спокойно осмотреться.
                    {me.role === "owner" ? " Когда будете готовы — включите агента в настройках." : " Включает владелец."}</span>
                  {me.role === "owner" && page !== "settings" && <Button size="sm" variant="outline" onClick={() => setPage("settings")}>Открыть настройки</Button>}
                </div>
              )}
              {page === "loads" && <LoadsPage />}
              {page === "dialogs" && <DialogsPage />}
              {page === "trucks" && <TrucksPage />}
              {page === "knowledge" && <KnowledgePage />}
              {page === "settings" && me.role === "owner" && <SettingsPage />}
              {page === "users" && me.role === "owner" && <UsersPage me={me} />}
            </div>
            <aside className="hidden w-72 shrink-0 2xl:block">
              <div className="sticky top-6">
                <div className="mb-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">Что делает ассистент</div>
                <div className="flex max-h-[calc(100vh-180px)] flex-col gap-2 overflow-y-auto pr-1">
                  {s?.log.map((l, i) => (
                    <div key={i} className={cn("rounded-md border bg-card p-2.5 text-xs",
                      l.kind === "error" && "border-destructive/30 text-destructive",
                      l.kind === "ready" && "border-emerald-300 bg-emerald-50",
                      l.kind === "learn" && "border-violet-200 bg-violet-50",
                      l.kind === "agent" && "border-primary/20 bg-accent/60")}>
                      <div>{l.text}</div>
                      <div className="mt-1 text-muted-foreground">{ago(l.ts)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </aside>
          </div>
        </main>
        <Toaster position="bottom-right" />
      </div>
    </TooltipProvider>
  )
}

function Stat({ icon: Icon, label, value, hint, accent }: { icon: typeof Radar; label: string; value?: number | string; hint: string; accent?: boolean }) {
  return (
    <div className="bg-card px-5 py-4 md:px-8">
      <div className="flex items-center gap-2 text-xs text-muted-foreground"><Icon className="size-3.5" />{label}</div>
      <div className={cn("num mt-1 text-2xl font-semibold", accent && "text-primary")}>{value ?? "—"}</div>
      <div className="text-xs text-muted-foreground">{hint}</div>
    </div>
  )
}
