import { VoiceDaemonOrb, type VoiceDaemonState, type VoiceSignal } from './voice-daemon-orb';

export type VoiceComposerState = 'idle' | 'connecting' | 'listening' | 'transcribing'
    | 'sending' | 'done' | 'error';

export interface VoiceComposerOptions {
    readonly placeholder?: string;
    readonly initialValue?: string;
    readonly ariaLabel?: string;
    readonly serviceUrl?: string;
    readonly driver?: VoiceComposerDriver;
    readonly onTextChange?: (text: string) => void;
    readonly onSubmit?: (text: string) => void;
}

export interface VoiceComposerDriver {
    start(onSignal: (signal: VoiceSignal) => void): Promise<void>;
    stop(): Promise<{ readonly text: string; readonly backend?: string; readonly device?: string }>;
}

interface Capability {
    readonly ok: boolean;
    readonly backend?: string;
    readonly device?: string;
    readonly reason?: string;
    readonly capture?: { readonly browser_wav?: boolean; readonly native_sox?: boolean };
}

interface Transcription {
    readonly ok: boolean;
    readonly text?: string;
    readonly error?: string;
    readonly backend?: string;
    readonly device?: string;
}

class PcmRecorder {
    private context?: AudioContext;
    private stream?: MediaStream;
    private source?: MediaStreamAudioSourceNode;
    private processor?: ScriptProcessorNode;
    private sink?: GainNode;
    private samples: Float32Array[] = [];
    private inputRate = 48_000;
    private onSignal?: (signal: VoiceSignal) => void;

    async start(onSignal: (signal: VoiceSignal) => void): Promise<void> {
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        this.context = new AudioContext();
        this.inputRate = this.context.sampleRate;
        this.onSignal = onSignal;
        this.source = this.context.createMediaStreamSource(this.stream);
        this.processor = this.context.createScriptProcessor(4_096, 1, 1);
        this.sink = this.context.createGain();
        this.sink.gain.value = 0;
        this.processor.onaudioprocess = event => {
            const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
            this.samples.push(chunk);
            let energy = 0;
            let crossings = 0;
            for (let index = 0; index < chunk.length; index++) {
                const sample = chunk[index];
                energy += sample * sample;
                if (index && (sample >= 0) !== (chunk[index - 1] >= 0)) crossings++;
            }
            const rms = Math.sqrt(energy / chunk.length);
            const db = 20 * Math.log10(Math.max(rms, 1e-6));
            const level = Math.min(1, Math.max(0, (db + 56) / 41));
            const zcr = crossings / Math.max(1, chunk.length - 1);
            const tone = level > .15 ? Math.min(1, Math.max(0, (zcr - .03) / .25)) : .35;
            this.onSignal?.({ level, tone, hearing: level > .08 });
        };
        this.source.connect(this.processor);
        this.processor.connect(this.sink);
        this.sink.connect(this.context.destination);
    }

    async stop(): Promise<Blob> {
        this.processor?.disconnect();
        this.source?.disconnect();
        this.sink?.disconnect();
        this.stream?.getTracks().forEach(track => track.stop());
        await this.context?.close();
        const length = this.samples.reduce((sum, chunk) => sum + chunk.length, 0);
        const joined = new Float32Array(length);
        let offset = 0;
        for (const chunk of this.samples) {
            joined.set(chunk, offset);
            offset += chunk.length;
        }
        return encodeWav(resample(joined, this.inputRate, 16_000), 16_000);
    }
}

function resample(input: Float32Array, sourceRate: number, targetRate: number): Float32Array {
    if (sourceRate === targetRate) return input;
    const output = new Float32Array(Math.max(1, Math.round(input.length * targetRate / sourceRate)));
    for (let index = 0; index < output.length; index++) {
        const position = index * sourceRate / targetRate;
        const left = Math.floor(position);
        const right = Math.min(input.length - 1, left + 1);
        const fraction = position - left;
        output[index] = input[left] * (1 - fraction) + input[right] * fraction;
    }
    return output;
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const write = (offset: number, value: string) => {
        for (let index = 0; index < value.length; index++) view.setUint8(offset + index, value.charCodeAt(index));
    };
    write(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    write(8, 'WAVE');
    write(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    write(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let index = 0; index < samples.length; index++) {
        const sample = Math.max(-1, Math.min(1, samples[index]));
        view.setInt16(44 + index * 2, sample < 0 ? sample * 32_768 : sample * 32_767, true);
    }
    return new Blob([buffer], { type: 'audio/wav' });
}

function messageFor(error: unknown): string {
    if (error instanceof DOMException && error.name === 'NotAllowedError') return 'Microphone permission was denied';
    return error instanceof Error ? error.message : String(error);
}

export class VoiceComposer {
    readonly root: HTMLElement;
    readonly textarea: HTMLTextAreaElement;
    private readonly dictate: HTMLButtonElement;
    private readonly send: HTMLButtonElement;
    private readonly status: HTMLElement;
    private readonly serviceUrl: string;
    private readonly options: VoiceComposerOptions;
    private readonly orb: VoiceDaemonOrb;
    private state: VoiceComposerState = 'idle';
    private recorder?: PcmRecorder;
    private nativeSession?: string;

    constructor(host: HTMLElement, options: VoiceComposerOptions = {}) {
        this.options = options;
        this.serviceUrl = (options.serviceUrl || localStorage.getItem('dirac:voice:endpoint')
            || 'http://127.0.0.1:8903').replace(/\/$/, '');
        this.root = document.createElement('section');
        this.root.className = 'voice-composer';
        this.root.dataset.state = this.state;
        this.textarea = document.createElement('textarea');
        this.textarea.className = 'voice-composer__input';
        this.textarea.placeholder = options.placeholder || 'Ask, describe, or dictate…';
        this.textarea.value = options.initialValue || '';
        this.textarea.setAttribute('aria-label', options.ariaLabel || 'Message');

        const rail = document.createElement('div');
        rail.className = 'voice-composer__rail';
        this.dictate = document.createElement('button');
        this.dictate.type = 'button';
        this.dictate.className = 'voice-composer__dictate';
        this.orb = new VoiceDaemonOrb(this.dictate, () => {
            if (this.state === 'done' || this.state === 'error') {
                this.setState('idle', 'Voice ready');
            } else {
                this.root.dataset.daemonState = 'idle';
                this.status.textContent = 'Voice ready';
            }
        });
        this.dictate.setAttribute('aria-label', 'Start voice dictation');
        this.status = document.createElement('span');
        this.status.className = 'voice-composer__status';
        this.status.textContent = 'Voice ready';
        this.status.id = `voice-composer-status-${Math.random().toString(36).slice(2, 9)}`;
        this.status.setAttribute('aria-live', 'polite');
        this.dictate.setAttribute('aria-describedby', this.status.id);
        this.send = document.createElement('button');
        this.send.type = 'button';
        this.send.className = 'voice-composer__send';
        this.send.setAttribute('aria-label', 'Send');
        this.send.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 15.7V4.3m0 0L5.6 8.7M10 4.3l4.4 4.4"/></svg>';
        rail.append(this.status, this.dictate, this.send);
        this.root.append(this.textarea, rail);
        host.append(this.root);

        this.textarea.addEventListener('input', () => this.textChanged());
        this.textarea.addEventListener('keydown', event => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                this.submit();
            }
        });
        this.dictate.addEventListener('click', () => void this.toggleDictation());
        this.send.addEventListener('click', () => this.submit());
        this.textChanged(false);
    }

    private setState(state: VoiceComposerState, message: string): void {
        this.state = state;
        this.root.dataset.state = state;
        this.status.textContent = message;
        this.dictate.setAttribute('aria-label', state === 'listening' ? 'Stop voice dictation' : 'Start voice dictation');
        this.dictate.setAttribute('aria-pressed', String(state === 'listening'));
        this.dictate.disabled = state === 'connecting' || state === 'transcribing' || state === 'sending';
        this.root.setAttribute('aria-busy', String(
            state === 'connecting' || state === 'transcribing' || state === 'sending'));
        const daemonState: VoiceDaemonState = state === 'connecting' ? 'calibrating'
            : state === 'listening' ? 'recording'
            : state === 'transcribing' ? 'transcribing'
            : state === 'sending' ? 'sending'
            : state === 'done' ? 'done'
            : state === 'error' ? 'fail' : 'idle';
        this.root.dataset.daemonState = daemonState;
        this.orb.setState(daemonState);
    }

    private textChanged(notify = true): void {
        this.send.disabled = !this.textarea.value.trim();
        if (notify) this.options.onTextChange?.(this.textarea.value);
    }

    private async capability(): Promise<Capability> {
        const response = await fetch(`${this.serviceUrl}/v1/voice/capabilities`, { cache: 'no-store' });
        const payload = await response.json() as Capability;
        if (!response.ok || !payload.ok) throw new Error(payload.reason || 'Voice backend is unavailable');
        return payload;
    }

    private async toggleDictation(): Promise<void> {
        if (this.state === 'listening') {
            await this.stopDictation();
            return;
        }
        try {
            this.setState('connecting', 'Connecting to Voice Daemon…');
            if (this.options.driver) {
                await this.options.driver.start(signal => {
                    this.orb.setSignal(signal);
                    this.root.style.setProperty('--voice-level',
                        Math.min(1, Math.max(0, signal.level)).toFixed(3));
                });
                this.setState('listening', 'Listening · tap Stop when finished');
                return;
            }
            const capability = await this.capability();
            if (window.isSecureContext && typeof navigator.mediaDevices?.getUserMedia === 'function') {
                this.recorder = new PcmRecorder();
                await this.recorder.start(signal => {
                    this.orb.setSignal(signal);
                    this.root.style.setProperty('--voice-level', signal.level.toFixed(3));
                });
            } else if (capability.capture?.native_sox) {
                const response = await fetch(`${this.serviceUrl}/v1/voice/sessions`, { method: 'POST' });
                const payload = await response.json() as { ok: boolean; session_id?: string; error?: string };
                if (!response.ok || !payload.session_id) throw new Error(payload.error || 'Could not start native capture');
                this.nativeSession = payload.session_id;
            } else {
                throw new Error('This HTTP page cannot open the microphone and the Mac native helper is not enabled');
            }
            this.setState('listening', 'Listening · tap Stop when finished');
        } catch (error) {
            this.setState('error', messageFor(error));
        }
    }

    private async stopDictation(): Promise<void> {
        try {
            this.setState('transcribing', 'Transcribing locally…');
            let result: Transcription;
            if (this.options.driver) {
                result = { ok: true, ...await this.options.driver.stop() };
            } else if (this.nativeSession) {
                const session = this.nativeSession;
                this.nativeSession = undefined;
                const response = await fetch(`${this.serviceUrl}/v1/voice/sessions/${encodeURIComponent(session)}`, { method: 'DELETE' });
                result = await response.json() as Transcription;
                if (!response.ok) throw new Error(result.error || 'Transcription failed');
            } else if (this.recorder) {
                const recorder = this.recorder;
                this.recorder = undefined;
                const wav = await recorder.stop();
                const response = await fetch(`${this.serviceUrl}/v1/voice/transcriptions?language=auto`, {
                    method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav,
                });
                result = await response.json() as Transcription;
                if (!response.ok) throw new Error(result.error || 'Transcription failed');
            } else {
                throw new Error('No recording session is active');
            }
            if (!result.ok) throw new Error(result.error || 'Transcription failed');
            if (!result.text) {
                this.setState('idle', 'No speech detected');
                return;
            }
            this.insertAtCursor(result.text);
            this.setState('done', `${result.backend || 'Whisper'} · ${result.device || 'local'} · inserted`);
        } catch (error) {
            this.setState('error', messageFor(error));
        } finally {
            this.root.style.removeProperty('--voice-level');
        }
    }

    private insertAtCursor(text: string): void {
        const start = this.textarea.selectionStart;
        const end = this.textarea.selectionEnd;
        const before = this.textarea.value.slice(0, start);
        const after = this.textarea.value.slice(end);
        const prefix = before && !/\s$/.test(before) ? ' ' : '';
        const suffix = after && !/^\s/.test(after) ? ' ' : '';
        this.textarea.value = `${before}${prefix}${text}${suffix}${after}`;
        const caret = before.length + prefix.length + text.length + suffix.length;
        this.textarea.setSelectionRange(caret, caret);
        this.textarea.focus();
        this.textChanged();
    }

    private submit(): void {
        const text = this.textarea.value.trim();
        if (!text) return;
        this.setState('sending', 'Sending…');
        this.options.onSubmit?.(text);
        setTimeout(() => this.setState('done', 'Sent'), 520);
    }

    /** Host-facing state hook: lets a real daemon drive the original v8 visual vocabulary. */
    setDaemonState(state: VoiceDaemonState, signal?: VoiceSignal): void {
        if (signal) this.orb.setSignal(signal);
        const message: Record<VoiceDaemonState, string> = {
            idle: 'Voice ready',
            off: 'Voice unavailable',
            calibrating: 'Calibrating…',
            recording: 'Listening',
            transcribing: 'Transcribing…',
            sending: 'Sending…',
            done: 'Done',
            fail: 'Failed',
            error: 'Error',
            no_speech: 'No speech detected',
            filtered: 'Speech filtered',
        };
        const visualState: VoiceComposerState = state === 'recording' ? 'listening'
            : state === 'calibrating' ? 'connecting'
            : state === 'transcribing' ? 'transcribing'
            : state === 'sending' ? 'sending'
            : state === 'done' ? 'done'
            : ['fail', 'error', 'no_speech', 'filtered'].includes(state) ? 'error' : 'idle';
        this.state = visualState;
        this.root.dataset.state = visualState;
        this.root.dataset.daemonState = state;
        this.root.setAttribute('aria-busy', String(
            ['calibrating', 'transcribing', 'sending'].includes(state)));
        this.dictate.setAttribute('aria-pressed', String(state === 'recording'));
        this.dictate.setAttribute('aria-label', state === 'recording'
            ? 'Stop voice dictation' : 'Start voice dictation');
        this.dictate.disabled = ['calibrating', 'transcribing', 'sending'].includes(state);
        this.status.textContent = message[state];
        this.orb.setState(state);
    }

    /** Explicit chromatic-system hook; returns false when the requested system is unknown. */
    setVoicePalette(id: string): boolean {
        return this.orb.setPalette(id);
    }

    /** Idle-motion hook; state sizes, geometry, and the 56px interaction target stay unchanged. */
    setVoiceIdleMotionPreset(id: string): boolean {
        return this.orb.setIdleMotionPreset(id);
    }

    /** Idle-only optical size. Listening, processing, and terminal sizes are unaffected. */
    setVoiceIdleSizePreset(id: string): boolean {
        return this.orb.setIdleSizePreset(id);
    }

    /** Selects one of the independently drawn Sending topologies. */
    setVoiceSendingVariant(id: string): boolean {
        return this.orb.setSendingVariant(id);
    }

    /** Sending-only optical size; the 56px interaction target remains unchanged. */
    setVoiceSendingSizePreset(id: string): boolean {
        return this.orb.setSendingSizePreset(id);
    }
}
