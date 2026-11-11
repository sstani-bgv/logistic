import { useEffect, useState } from "react"
import { BookOpenCheck, MessagesSquare, PackageSearch, Truck as TruckIcon, Radar, Clock, Filter, Sparkles } from "lucide-react"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { usePoll, ago, type Stats } from "@/lib/api"
import { cn } from "@/lib/utils"
import LoadsPage from "@/pages/Loads"
import DialogsPage from "@/pages/Dialogs"
import TrucksPage from "@/pages/Trucks"
import KnowledgePage from "@/pages/Knowledge"

const NAV = [
  { id: "loads", label: "Грузы", icon: PackageSearch },
  { id: "dialogs", label: "Переговоры", icon: MessagesSquare },
  { id: "trucks", label: "Машины", icon: TruckIcon },
  { id: "knowledge", label: "База знаний", icon: BookOpenCheck },
] as const
type Page = (typeof NAV)[number]["id"]

export default function App() {
  const [page, setPage] = useState<Page>(() => (NAV.find((n) => n.id === location.hash.slice(1))?.id ?? "loads"))
  useEffect(() => { location.hash = page }, [page])
  const { data: s } = usePoll<Stats>("/stats", 4000)

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
            {NAV.map((n) => (
              <button key={n.id} onClick={() => setPage(n.id)}
                className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  page === n.id ? "bg-accent font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                <n.icon className="size-4" />
                <span className="flex-1 text-left">{n.label}</span>
                {!!counters[n.id] && <span className="num rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{counters[n.id]}</span>}
              </button>
            ))}
          </nav>
          <div className="mt-auto rounded-lg border bg-muted/40 p-3 text-xs">
            <div className="mb-1 flex items-center gap-1.5 font-medium"><span className="size-2 animate-pulse rounded-full bg-emerald-500" />Мониторинг АТИ включён</div>
            <div className="text-muted-foreground">{s?.me?.name ? `Аккаунт: ${s.me.name}` : "Подключение…"}</div>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <header className="border-b bg-card">
            <div className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
              <Stat icon={Radar} label="Просмотрено ботом сегодня" value={s?.views} hint={`${s?.scans ?? 0} проходов по бирже`} />
              <Stat icon={Filter} label="Отсеяно автоматически" value={s?.filtered} hint="не подходят по правилам" />
              <Stat icon={Sparkles} label="Подходящих грузов" value={s?.relevant} hint={`${s?.ready ?? 0} готовы к звонку`} accent />
              <Stat icon={Clock} label="Рутины снято с логиста" value={s ? `${s.hours_saved} ч` : undefined} hint="≈25 сек на просмотр груза" />
            </div>
          </header>
          <div className="flex gap-6 px-4 py-6 md:px-8">
            <div className="min-w-0 flex-1">
              {page === "loads" && <LoadsPage />}
              {page === "dialogs" && <DialogsPage />}
              {page === "trucks" && <TrucksPage />}
              {page === "knowledge" && <KnowledgePage />}
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
