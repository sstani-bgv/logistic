import { useEffect, useRef, useState } from "react"
import { Loader2, Mic, PhoneOff } from "lucide-react"
import { Button } from "@/components/ui/button"

// Тестовый звонок: логист играет заказчика, агент говорит голосом по сценарию диалога (Yandex Realtime через сервер).
// Звук — как у голосового менеджера на сайте (ai-managers-site/site/demo.js): PCM16 44,1 кГц, перебивать можно.
const RATE = 44100

type Call = {
  ctx: AudioContext; out: GainNode; ws?: WebSocket; stream?: MediaStream
  srcs: AudioBufferSourceNode[]; playAt: number; item: string | null; itemAt: number; live: boolean
}
type Ev = { type: string; delta?: string; item_id?: string; text?: string; transcript?: string; seconds?: number; rub?: number }

const b64 = (buf: ArrayBuffer) => {
  const u = new Uint8Array(buf)
  let s = ""
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000))
  return btoa(s)
}

export default function VoiceTest({ dialogId }: { dialogId: number }) {
  const [state, setState] = useState<"idle" | "connecting" | "listening" | "speaking">("idle")
  const [result, setResult] = useState<{ text: string; transcript?: string; seconds?: number; rub?: number } | null>(null)
  const call = useRef<Call | null>(null)
  const supported = typeof window !== "undefined" && !!navigator.mediaDevices && "AudioWorkletNode" in window

  const hang = (r?: { text: string; transcript?: string; seconds?: number; rub?: number }) => {
    const c = call.current
    call.current = null
    if (c) {
      c.ws?.close()
      c.stream?.getTracks().forEach((t) => t.stop())
      c.ctx.close().catch(() => {})
    }
    setState("idle")
    if (r) setResult(r)
  }
  useEffect(() => () => hang(), [])  // закрыли окно — кладём трубку

  const send = (c: Call, ev: object) => { if (c.ws?.readyState === 1) c.ws.send(JSON.stringify(ev)) }

  const play = (c: Call, ev: Ev) => {
    const bin = atob(ev.delta ?? ""), n = bin.length >> 1
    if (!c.live || !n) return  // хвост ответа, который перебили
    const buf = c.ctx.createBuffer(1, n, RATE), f = buf.getChannelData(0)
    for (let i = 0; i < n; i++) f[i] = ((bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)) << 16 >> 16) / 32768
    const src = c.ctx.createBufferSource(), now = c.ctx.currentTime, at = c.playAt > now ? c.playAt : now + 0.1
    src.buffer = buf
    src.connect(c.out)
    src.start(at)
    if (ev.item_id !== c.item) { c.item = ev.item_id ?? null; c.itemAt = at }
    c.playAt = at + buf.duration
    c.srcs.push(src)
    src.onended = () => {
      c.srcs.splice(c.srcs.indexOf(src), 1)
      if (call.current === c && !c.srcs.length && !c.live) setState("listening")
    }
    setState("speaking")
  }

  const interrupt = (c: Call) => {  // собеседник заговорил — агент замолкает сразу
    if (c.live) send(c, { type: "response.cancel" })
    if (c.srcs.length) {
      const ms = Math.max(0, Math.round((c.ctx.currentTime - c.itemAt) * 1000))
      c.srcs.forEach((s) => { s.onended = null; s.stop() })
      c.srcs = []
      c.playAt = 0
      if (c.item) send(c, { type: "conversation.item.truncate", item_id: c.item, audio_end_ms: ms })
    }
    c.live = false
    setState("listening")
  }

  const start = async () => {
    setResult(null)
    const ctx = new AudioContext()
    ctx.resume().catch(() => {})
    const out = ctx.createGain()
    out.connect(ctx.destination)
    const c: Call = { ctx, out, srcs: [], playAt: 0, item: null, itemAt: 0, live: false }
    call.current = c
    setState("connecting")
    try {
      c.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    } catch (e) {
      const name = e instanceof DOMException ? e.name : ""
      const text = name === "NotAllowedError" ? "Нет доступа к микрофону — разрешите его в браузере."
        : name === "NotFoundError" ? "Микрофон не найден." : name === "NotReadableError" ? "Микрофон занят другой программой."
        : `Микрофон не включился${name ? ` (${name})` : ""}.`
      if (call.current === c) hang({ text })
      return
    }
    if (call.current !== c) { c.stream.getTracks().forEach((t) => t.stop()); return }
    try {
      await ctx.audioWorklet.addModule("/mic-worklet.js")
    } catch (e) {
      if (call.current === c) hang({ text: `Звук в этом браузере не запустился${e instanceof Error ? ` (${e.message})` : ""}.` })
      return
    }
    if (call.current !== c) return
    const ws = (c.ws = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/voice/test/${dialogId}`))
    ws.onmessage = (e) => {
      if (call.current !== c) return
      let ev: Ev
      try { ev = JSON.parse(e.data) } catch { return }
      if (ev.type === "response.created") c.live = true
      else if (ev.type === "response.output_audio.delta") play(c, ev)
      else if (ev.type === "input_audio_buffer.speech_started") interrupt(c)
      else if (ev.type === "response.done") { c.live = false; if (!c.srcs.length) setState("listening") }
      else if (ev.type === "end") hang({ text: ev.text ?? "Звонок завершён.", transcript: ev.transcript, seconds: ev.seconds, rub: ev.rub })
    }
    ws.onclose = () => { if (call.current === c) hang({ text: "Связь прервалась." }) }
    const node = new AudioWorkletNode(ctx, "mic", { numberOfOutputs: 0 })
    node.port.onmessage = (e) => send(c, { type: "input_audio_buffer.append", audio: b64(e.data) })
    ctx.createMediaStreamSource(c.stream).connect(node)
  }

  if (!supported) return null
  const busy = state !== "idle"
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {busy
          ? <Button size="sm" variant="destructive" onClick={() => hang({ text: "Звонок завершён." })}><PhoneOff />Положить трубку</Button>
          : <Button size="sm" variant="outline" onClick={start}><Mic />Тестовый звонок: вы — заказчик</Button>}
        <span role="status" aria-live="polite" className="text-xs text-muted-foreground">
          {state === "connecting" ? <span className="inline-flex items-center gap-1"><Loader2 className="size-3 animate-spin" />Подключаюсь…</span>
            : state === "speaking" ? "Говорит агент — можно перебить" : state === "listening" ? "Слушаю вас" : "Агент позвонит вам голосом по сценарию этого диалога. Заказчику ничего не уйдёт."}
        </span>
      </div>
      {result && (
        <div className="text-xs">
          <div className="text-muted-foreground">{result.text}{result.seconds ? ` · ${result.seconds} с · ≈${result.rub} ₽` : ""}</div>
          {result.transcript && <div className="mt-1 whitespace-pre-wrap rounded bg-muted/50 p-2">{result.transcript}</div>}
        </div>
      )}
    </div>
  )
}
