// Микрофон → PCM16 44,1 кГц кусками по 100 мс при любой частоте AudioContext (как на сайте Стаса).
// Отдельный файл, а не blob: — так его пропускает CSP (script-src 'self').
const RATE = 44100
registerProcessor('mic', class extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / RATE; this.t = 0; this.p = 0; this.o = new Int16Array(RATE / 10); this.n = 0 }
  process([[x]]) {
    if (!x) return true
    for (; this.t < x.length - 1; this.t += this.step) {
      const i = Math.floor(this.t), a = i < 0 ? this.p : x[i]
      this.o[this.n++] = Math.max(-1, Math.min(1, a + (x[i + 1] - a) * (this.t - i))) * 32767
      if (this.n === RATE / 10) { this.port.postMessage(this.o.buffer, [this.o.buffer]); this.o = new Int16Array(RATE / 10); this.n = 0 }
    }
    this.t -= x.length
    this.p = x[x.length - 1]
    return true
  }
})
