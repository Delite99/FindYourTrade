import { useState, useRef, useEffect } from 'react';
import { api } from '../api/client.js';
import { WavRecorder } from '../api/wavRecorder.js';
import { ListingCard } from './cards.jsx';

const GREETING = 'Hello how can i help you today?';
const LS_KEY = 'fyt_chat_threads';

function newThread() {
  return {
    id: 't-' + Math.random().toString(36).slice(2, 10),
    title: 'New chat',
    created: Date.now(),
    messages: [{ role: 'assistant', content: GREETING, spoken: GREETING }],
  };
}

function initialThreads() {
  // One shared id source: threads[0].id IS the active id. The old code called
  // the loader twice (useState(loadThreads) runs it EVERY render as the
  // initializer), minting two different "first" threads — messages went to a
  // ghost thread and the screen never updated.
  const t = newThread();
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length && arr[0]?.id && Array.isArray(arr[0]?.messages)) {
        return arr;
      }
    }
  } catch {}
  return [t];
}

export default function ChatTab({ compareSel, toggleCompare }) {
  const [threads, setThreads] = useState(initialThreads);
  const [activeId, setActiveId] = useState(null); // null = follow threads[0]
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [busy, setBusy] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState(true);
  const [handsFree, setHandsFree] = useState(true);
  const [voices, setVoices] = useState([]);
  const [voiceURI, setVoiceURI] = useState(() => {
    try { return localStorage.getItem('fy_voice_uri') || ''; } catch { return ''; }
  });
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [activeListingId, setActiveListingId] = useState(null);
  const [sidebar, setSidebar] = useState(false);

  const recognitionRef = useRef(null);
  const wavRecorderRef = useRef(null);
  const transcriptAccumulatorRef = useRef('');
  const silenceTimerRef = useRef(null);
  const autoSpeakRef = useRef(autoSpeak);
  const handsFreeRef = useRef(handsFree);
  const activeIdRef = useRef(null); // set from resolvedId below
  const threadsRef = useRef(threads);
  const activeListingIdRef = useRef(activeListingId);
  const scrollRef = useRef(null);

  useEffect(() => { autoSpeakRef.current = autoSpeak; }, [autoSpeak]);
  useEffect(() => { handsFreeRef.current = handsFree; }, [handsFree]);
  useEffect(() => { threadsRef.current = threads; }, [threads]);
  useEffect(() => { activeListingIdRef.current = activeListingId; }, [activeListingId]);

  const resolvedId = activeId || threads[0]?.id;
  activeIdRef.current = resolvedId;
  const thread = threads.find((t) => t.id === resolvedId) || threads[0];
  const messages = thread?.messages || [];

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(threadsRef.current)); } catch {}
  }, [threads]);

  // Stick to the newest message: jump instantly while streaming in, and only
  // steal the scroll when the user is already near the bottom (so reading
  // older messages never yanks away mid-read).
  function stickToBottom(smooth = false) {
    const el = scrollRef.current;
    if (!el) return;
    try {
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    } catch {
      el.scrollTop = el.scrollHeight;
    }
  }
  function nearBottom(px = 120) {
    const el = scrollRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < px;
  }
  const stickRef = useRef(true);
  function onScroll() { stickRef.current = nearBottom(); }
  useEffect(() => {
    if (stickRef.current) stickToBottom();
  }, [messages.length, busy, transcribing, isSpeaking, activeId]);
  // Live transcript and spoken text grow mid-message — follow them too.
  useEffect(() => {
    if (stickRef.current && (liveTranscript || transcribing || busy)) stickToBottom();
  }, [liveTranscript, transcribing, busy]);

  function patchThread(id, fn) {
    setThreads((prev) => prev.map((t) => (t.id === id ? fn(t) : t)));
  }

  function titleFor(firstUser) {
    const s = (firstUser || '').trim();
    return s.length > 42 ? s.slice(0, 42) + '…' : s || 'New chat';
  }

  // Clean text for speech synthesis
  function cleanForSpeech(str) {
    if (!str) return '';
    return str
      .replace(/[#*_~`]/g, '')
      .replace(/⚠️/g, 'Warning: ')
      .replace(/MWK/g, 'Malawi Kwacha')
      .replace(/\bkm\b/g, 'kilometers')
      .replace(/\bl(\d+)\b/gi, 'listing $1')
      .replace(/https?:\/\/\S+/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function speakBrowser(textToSpeak) {
    if (typeof window === 'undefined' || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const clean = cleanForSpeech(textToSpeak);
      if (!clean) return;
      const utterance = new SpeechSynthesisUtterance(clean);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      const voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => {
        setIsSpeaking(false);
        // Hands-free: when the answer finishes, open the mic for the reply.
        if (handsFreeRef.current) {
          setTimeout(() => { startRecording(); }, 450);
        }
      };
      utterance.onerror = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn('Speech synthesis error:', err);
      setIsSpeaking(false);
    }
  }

  function pickVoice() {
    try {
      const available = window.speechSynthesis.getVoices() || [];
      const chosen = voiceURI ? available.find((v) => v.voiceURI === voiceURI) : null;
      return chosen
        || available.find(
          (v) => v.lang.startsWith('en') && (v.name.includes('Natural') || v.name.includes('Google') || v.default)
        ) || available.find((v) => v.lang.startsWith('en')) || null;
    } catch { return null; }
  }

  function speakBack(textToSpeak) { speakBrowser(textToSpeak); }

  function stopSpeaking() {
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel();
      setIsSpeaking(false);
    }
  }

  useEffect(() => {
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      const load = () => setVoices(window.speechSynthesis.getVoices() || []);
      load();
      window.speechSynthesis.onvoiceschanged = load;
    }
    return () => {
      if (typeof window !== 'undefined' && window.speechSynthesis) {
        window.speechSynthesis.cancel();
        window.speechSynthesis.onvoiceschanged = null;
      }
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    };
  }, []);

  async function sendMessage(query) {
    if (!query || !query.trim() || busy) return;
    const id = activeIdRef.current;
    const userMsg = { role: 'user', content: query.trim() };
    const before = threadsRef.current.find((t) => t.id === id);
    const history = [...(before?.messages || []), userMsg];
    const firstUser = history.find((m) => m.role === 'user')?.content;
    patchThread(id, (t) => ({ ...t, messages: history, title: titleFor(firstUser) }));
    setText('');
    setBusy(true);
    stopSpeaking();
    try {
      const r = await api.chat(history, activeListingIdRef.current);
      if (r.listing_id) setActiveListingId(r.listing_id);
      const assistantMsg = {
        role: 'assistant',
        content: r.answer,
        spoken: r.spoken || r.answer,
        listing_id: r.listing_id || activeListingIdRef.current,
        results: (r.show_results && r.results) || [],
      };
      patchThread(id, (t) => ({ ...t, messages: [...t.messages, assistantMsg] }));
      if (autoSpeakRef.current) speakBack(r.spoken || r.answer);
    } catch (e) {
      const status = Number(e?.status || 0);
      const offline = e?.name === 'TypeError' || status === 502 || status === 503;
      const errMsg = offline
        ? 'I could not reach the trade backend. It may be restarting — give it a few seconds and send that again.'
        : status >= 500
          ? `The trade backend hit a server error (HTTP ${status}). Please try again in a moment.`
          : `${e?.message || 'Something went wrong on that request.'}`;
      patchThread(id, (t) => ({ ...t, messages: [...t.messages, { role: 'assistant', content: errMsg, spoken: errMsg }] }));
      if (autoSpeakRef.current) speakBack(errMsg);
    } finally {
      setBusy(false);
    }
  }

  function startNewChat() {
    const t = newThread();
    setThreads((prev) => [t, ...prev]);
    setActiveId(t.id);
    setText('');
    setSidebar(false);
  }

  function deleteThread(id) {
    setThreads((prev) => {
      const next = prev.filter((t) => t.id !== id);
      const kept = next.length ? next : [newThread()];
      if (id === activeIdRef.current) setActiveId(kept[0].id);
      return kept;
    });
  }

  async function startRecording() {
    stopSpeaking();
    // Barge-in guard: if the recogniser or recorder from a previous turn is
    // still alive, kill it before opening a new stream — leftover streams are
    // what wedge the mic half-open and stall the conversation.
    try { recognitionRef.current?.abort?.(); } catch {}
    try { recognitionRef.current?.stop?.(); } catch {}
    recognitionRef.current = null;
    try { wavRecorderRef.current?.stop?.(); } catch {}
    wavRecorderRef.current = null;
    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    setLiveTranscript('');
    transcriptAccumulatorRef.current = '';
    let hasSpeechRecognition = false;
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      try {
        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = navigator.language || 'en-US';
        recognition.onresult = (event) => {
          let interim = '';
          let final = '';
          for (let i = 0; i < event.results.length; ++i) {
            const transcript = event.results[i][0].transcript;
            if (event.results[i].isFinal) final += transcript + ' ';
            else interim += transcript;
          }
          const fullText = (final + interim).trim();
          if (fullText) {
            transcriptAccumulatorRef.current = fullText;
            setLiveTranscript(fullText);
            setText(fullText);
            if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
            silenceTimerRef.current = setTimeout(() => stopRecording(false), 1300);
          }
        };
        recognition.onerror = (e) => console.warn('Speech recognition warning:', e.error);
        recognition.start();
        recognitionRef.current = recognition;
        hasSpeechRecognition = true;
      } catch (err) {
        console.warn('SpeechRecognition failed to start:', err);
      }
    }
    try {
      const recorder = new WavRecorder();
      await recorder.start();
      wavRecorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      if (hasSpeechRecognition) setRecording(true);
      else alert('Microphone access is unavailable or denied in your browser settings.');
    }
  }

  async function stopRecording(cancel = false) {
    if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    setRecording(false);
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch {}
      recognitionRef.current = null;
    }
    let wavBlob = null;
    if (wavRecorderRef.current) {
      try { wavBlob = wavRecorderRef.current.stop(); } catch {}
      wavRecorderRef.current = null;
    }
    if (cancel) {
      setLiveTranscript('');
      return;
    }
    const browserText = (transcriptAccumulatorRef.current || liveTranscript || '').trim();
    setLiveTranscript('');
    if (wavBlob) {
      // Muse is now turning the audio into text — show it so the silence
      // between "Done speaking" and the reply never looks frozen.
      setTranscribing(true);
      try {
        const t = await api.transcribe(wavBlob, 'voice.wav');
        const text = (t.text || '').trim();
        setTranscribing(false);
        if (text) { await sendMessage(text); return; }
        if (browserText) { await sendMessage(browserText); return; }
        const msg = t.error?.error
          ? `Voice transcription failed: ${t.error.error}`
          : "I couldn't hear any speech in that recording. Please try again.";
        patchThread(activeIdRef.current, (th) => ({ ...th, messages: [...th.messages, { role: 'assistant', content: msg, spoken: msg }] }));
        return;
      } catch (e) {
        setTranscribing(false);
        if (browserText) { await sendMessage(browserText); return; }
        const msg = `Voice transcription failed (${e?.message || 'network error'}). Please try again.`;
        patchThread(activeIdRef.current, (th) => ({ ...th, messages: [...th.messages, { role: 'assistant', content: msg, spoken: msg }] }));
        return;
      }
    }
    if (browserText) await sendMessage(browserText);
  }

  return (
    <div className="gpt-shell">
      {/* ── Sidebar: chat history ── */}
      <aside className={`gpt-side ${sidebar ? 'open' : ''}`}>
        <button className="gpt-new" onClick={startNewChat}>+ New chat</button>
        <div className="gpt-history">
          {threads.map((t) => (
            <div key={t.id} className={`gpt-thread ${t.id === resolvedId ? 'on' : ''}`} onClick={() => { setActiveId(t.id); setSidebar(false); }}>
              <span className="gpt-thread-title">{t.title}</span>
              <button className="gpt-del" title="Delete chat" onClick={(e) => { e.stopPropagation(); deleteThread(t.id); }}>✕</button>
            </div>
          ))}
        </div>
        <div className="gpt-side-foot">
          <label className="gpt-voice-pick" title="Voice used to speak answers back">
            Voice
            <select
              value={voiceURI}
              onChange={(e) => {
                setVoiceURI(e.target.value);
                try { localStorage.setItem('fy_voice_uri', e.target.value); } catch {}
              }}
              disabled={voices.length === 0}
            >
              <option value="">Auto (best English)</option>
              {[...voices]
                .sort((a, b) => {
                  const aEn = a.lang.startsWith('en') ? 0 : 1;
                  const bEn = b.lang.startsWith('en') ? 0 : 1;
                  return aEn - bEn || a.name.localeCompare(b.name);
                })
                .map((v) => (
                  <option key={v.voiceURI} value={v.voiceURI}>{v.name} ({v.lang})</option>
                ))}
            </select>
          </label>
          <label className="gpt-speak">
            <input type="checkbox" checked={autoSpeak} onChange={(e) => {
              setAutoSpeak(e.target.checked);
              if (!e.target.checked) stopSpeaking();
            }} />
            Speak answers aloud
          </label>
          <label className="gpt-speak" title="After each answer, the mic opens by itself — just keep talking">
            <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} />
            Keep listening after answers
          </label>
        </div>
      </aside>

      {/* ── Main chat box ── */}
      <div className="gpt-main">
        <div className="gpt-top">
          <button className="gpt-burger" onClick={() => setSidebar((s) => !s)} aria-label="Chat history">☰</button>
          <strong>Assistant</strong>
          <span className="gpt-live">Muse Spark · live trader posts</span>
        </div>

        <div className="gpt-scroll" ref={scrollRef} onScroll={onScroll}>
          {messages.map((m, i) => (
            <div key={i}>
              <div className={`gpt-row ${m.role}`}>
                {m.role === 'assistant' && <div className="gpt-avatar" aria-hidden>✦</div>}
                <div className={`gpt-bubble ${m.role}`}>
                  <div className="gpt-text">{m.content}</div>
                  {m.role === 'assistant' && isSpeaking && (
                    <div className="gpt-tools">
                      <button onClick={stopSpeaking} title="Stop speaking">⏹ Stop</button>
                    </div>
                  )}
                </div>
              </div>
              {(m.results?.length > 0) && (
                <div className="listing-grid gpt-cards">
                  {m.results.map((l) => (
                    <ListingCard key={l.id} l={l}
                      compareSelected={compareSel?.find((x) => x.id === l.id)}
                      onCompare={toggleCompare} />
                  ))}
                </div>
              )}
            </div>
          ))}
          {busy && (
            <div className="gpt-row assistant">
              <div className="gpt-avatar" aria-hidden>✦</div>
              <div className="gpt-bubble assistant"><div className="gpt-dots"><span /><span /><span /></div></div>
            </div>
          )}
          {recording && (
            <div className="gpt-listening">
              <span className="gpt-pulse" />
              Listening — pause to send
              <div className="gpt-live-text">{liveTranscript || '…'}</div>
            </div>
          )}
          {transcribing && !busy && (
            <div className="gpt-row assistant">
              <div className="gpt-avatar" aria-hidden>✦</div>
              <div className="gpt-bubble assistant">
                <div className="gpt-text">Transcribing…</div>
                <div className="gpt-dots"><span /><span /><span /></div>
              </div>
            </div>
          )}
          {isSpeaking && (
            <div className="gpt-speaking">
              <span className="gpt-eq"><span /><span /><span /><span /></span>
              Speaking
              <button onClick={stopSpeaking}>⏹ Stop</button>
            </div>
          )}
        </div>

        {/* ── Composer: text + ChatGPT-style voice orb ── */}
        <div className="gpt-composer">
          <button
            className={`gpt-orb ${recording ? 'live' : ''}`}
            onClick={() => (recording ? stopRecording(true) : startRecording())}
            disabled={busy && !recording}
            title={recording ? 'Tap to cancel' : 'Tap to talk'}
            aria-label={recording ? 'Cancel recording' : 'Start voice conversation'}
          >
            {recording ? '■' : '🎙'}
          </button>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') sendMessage(text); }}
            placeholder="Message the assistant…"
            disabled={recording || busy}
            aria-label="Message the assistant"
          />
          <button className="gpt-send" onClick={() => sendMessage(text)} disabled={busy || recording || !text.trim()} aria-label="Send">↑</button>
        </div>
      </div>
    </div>
  );
}
