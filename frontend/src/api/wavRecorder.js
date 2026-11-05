// Records microphone audio straight to mono 16-bit PCM WAV.
//
// Muse Voice Transcribe only accepts mono 16-bit PCM WAV at 16 kHz or 24 kHz,
// and the backend container has no ffmpeg to convert anything else. Recording
// WAV in the browser removes that dependency entirely: MediaRecorder produces
// webm/opus, which would need server-side transcoding.

const TARGET_RATE = 16000;

function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);   // file size - 8
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);                       // PCM chunk size
  view.setUint16(20, 1, true);                        // format = PCM
  view.setUint16(22, 1, true);                        // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);           // byte rate
  view.setUint16(32, 2, true);                        // block align
  view.setUint16(34, 16, true);                       // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: 'audio/wav' });
}

export class WavRecorder {
  constructor() {
    this.chunks = [];
    this.recording = false;
    this.rate = TARGET_RATE;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    // Ask for 16 kHz; browsers may ignore it, so the real rate is read back.
    this.ctx = new Ctx({ sampleRate: TARGET_RATE });
    this.rate = this.ctx.sampleRate;

    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = (e) => {
      if (!this.recording) return;
      this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    source.connect(this.node);
    this.node.connect(this.ctx.destination);  // required for the node to run
    this.chunks = [];
    this.recording = true;
  }

  /** Stops capture and returns a WAV blob, or null when nothing was recorded. */
  stop() {
    this.recording = false;
    try { this.node?.disconnect(); } catch {}
    try { this.stream?.getTracks().forEach((t) => t.stop()); } catch {}
    try { this.ctx?.close(); } catch {}

    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    if (!total) return null;

    const flat = new Float32Array(total);
    let pos = 0;
    for (const c of this.chunks) { flat.set(c, pos); pos += c.length; }

    return encodeWav(resample(flat, this.rate, TARGET_RATE), TARGET_RATE);
  }
}
