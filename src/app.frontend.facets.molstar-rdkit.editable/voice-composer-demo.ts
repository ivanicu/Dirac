import { VoiceComposer, type VoiceComposerDriver } from '../app/components/voice-composer';
import { VOICE_IDLE_MOTION_PRESETS, VOICE_IDLE_SIZE_PRESETS, VOICE_PALETTE_SYSTEMS,
    VOICE_SENDING_SIZE_PRESETS, VOICE_SENDING_VARIANTS, VoiceDaemonOrb,
    type VoiceDaemonState,
    type VoiceSignal } from '../app/components/voice-daemon-orb';

class InteractionDriver implements VoiceComposerDriver {
    private timer?: ReturnType<typeof setInterval>;
    private tick = 0;

    async start(onSignal: (signal: VoiceSignal) => void): Promise<void> {
        await new Promise(resolve => setTimeout(resolve, 320));
        this.tick = 0;
        this.timer = setInterval(() => {
            this.tick += 1;
            const level = .18 + Math.abs(Math.sin(this.tick * .67)) * .68;
            onSignal({ level, tone: .24 + Math.abs(Math.sin(this.tick * .29)) * .62,
                hearing: level > .28 });
        }, 90);
    }

    async stop(): Promise<{ text: string; backend: string; device: string }> {
        if (this.timer) clearInterval(this.timer);
        await new Promise(resolve => setTimeout(resolve, 920));
        return {
            text: 'Compare the binding-site hydration pattern before promoting this design.',
            backend: 'Whisper',
            device: 'interaction preview',
        };
    }
}

const mount = () => {
    const host = document.getElementById('composer-mount');
    const toast = document.getElementById('demo-toast');
    const paletteGrid = document.getElementById('palette-grid');
    const paletteName = document.getElementById('palette-name');
    const paletteCharacter = document.getElementById('palette-character');
    const motionMatrix = document.getElementById('motion-matrix');
    const motionSlider = document.getElementById('motion-slider') as HTMLInputElement | null;
    const motionName = document.getElementById('motion-name');
    const motionCharacter = document.getElementById('motion-character');
    const idleSizeValue = document.getElementById('idle-size-value');
    const sendingGrid = document.getElementById('sending-grid');
    const fibonacciVariants = document.getElementById('fibonacci-variants');
    const sendingName = document.getElementById('sending-name');
    const sendingCharacter = document.getElementById('sending-character');
    const sendingSizeValue = document.getElementById('sending-size-value');
    if (!host || !toast || !paletteGrid || !paletteName || !paletteCharacter
        || !motionMatrix || !motionSlider || !motionName || !motionCharacter
        || !idleSizeValue || !sendingGrid || !fibonacciVariants || !sendingName
        || !sendingCharacter || !sendingSizeValue) return;
    const preview = new URLSearchParams(location.search);
    let currentDark = preview.get('theme') === 'dark';
    let currentPalette = VOICE_PALETTE_SYSTEMS.some(item => item.id === preview.get('palette'))
        ? preview.get('palette')! : 'graphite';
    let currentMotion = VOICE_IDLE_MOTION_PRESETS.some(item => item.id === preview.get('motion'))
        ? preview.get('motion')! : '04';
    let currentIdleSize = VOICE_IDLE_SIZE_PRESETS.some(item => item.id === preview.get('idle'))
        ? preview.get('idle')! : '40';
    let currentSending = VOICE_SENDING_VARIANTS.some(item => item.id === preview.get('sending'))
        ? preview.get('sending')! : '16';
    let currentSendingSize = VOICE_SENDING_SIZE_PRESETS.some(item => item.id === preview.get('sending-size'))
        ? preview.get('sending-size')! : '42';
    let composer: VoiceComposer;
    const sendingPreviews: VoiceDaemonOrb[] = [];
    const fibonacciPreviews: VoiceDaemonOrb[] = [];
    let showDaemonState: (state: VoiceDaemonState) => void = () => undefined;
    document.documentElement.dataset.voicePalette = currentPalette;

    const applyChromaticTokens = () => {
        const system = VOICE_PALETTE_SYSTEMS.find(item => item.id === currentPalette)
            || VOICE_PALETTE_SYSTEMS[0];
        const colours = currentDark ? system.dark : system.light;
        const root = document.documentElement.style;
        root.setProperty('--voice-idle-color', colours.idle);
        root.setProperty('--voice-off-color', colours.off);
        root.setProperty('--voice-calibrating-color', colours.calibrating);
        root.setProperty('--voice-record-color', colours.recordFar);
        root.setProperty('--voice-process-color', colours.processCyan);
        root.setProperty('--voice-sending-color', colours.sending);
        root.setProperty('--voice-done-color', colours.done);
        root.setProperty('--voice-no-speech-color', colours.noSpeech);
        root.setProperty('--voice-filtered-color', colours.filtered);
        root.setProperty('--voice-fail-color', colours.fail);
        root.setProperty('--accent', colours.idle);
        root.setProperty('--workspace-accent', colours.idle);
        paletteName.textContent = system.name;
        paletteCharacter.textContent = system.character;
    };

    const updateLocation = () => {
        const url = new URL(location.href);
        url.searchParams.set('theme', currentDark ? 'dark' : 'light');
        url.searchParams.set('palette', currentPalette);
        url.searchParams.delete('size');
        url.searchParams.set('motion', currentMotion);
        url.searchParams.set('idle', currentIdleSize);
        url.searchParams.set('sending', currentSending);
        url.searchParams.set('sending-size', currentSendingSize);
        history.replaceState({}, '', url);
    };

    const selectIdleSize = (id: string) => {
        const preset = VOICE_IDLE_SIZE_PRESETS.find(item => item.id === id);
        if (!preset) return;
        currentIdleSize = preset.id;
        composer.setVoiceIdleSizePreset(currentIdleSize);
        idleSizeValue.textContent = String(preset.target);
        document.querySelectorAll<HTMLButtonElement>('[data-idle-size]').forEach(button =>
            button.setAttribute('aria-pressed', String(button.dataset.idleSize === currentIdleSize)));
        showDaemonState('idle');
        updateLocation();
    };

    const selectMotion = (index: number) => {
        const preset = VOICE_IDLE_MOTION_PRESETS[index];
        if (!preset) return;
        currentMotion = preset.id;
        composer.setVoiceIdleMotionPreset(currentMotion);
        motionSlider.value = String(index + 1);
        motionSlider.style.setProperty('--motion-progress', `${index / 9 * 100}%`);
        motionName.textContent = `${preset.id} · ${preset.name}`;
        motionCharacter.textContent = `${preset.character} · contour ${Math.round(preset.contour * 100)}%`;
        document.querySelectorAll<HTMLButtonElement>('[data-motion-choice]').forEach(item =>
            item.setAttribute('aria-pressed', String(item.dataset.motionChoice === currentMotion)));
        document.querySelectorAll<HTMLElement>('[data-motion-cell]').forEach(item =>
            item.dataset.active = String(item.dataset.motionCell === currentMotion));
        showDaemonState('idle');
        updateLocation();
    };

    VOICE_IDLE_MOTION_PRESETS.forEach((preset, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'motion-column';
        button.dataset.motionChoice = preset.id;
        button.dataset.motionCell = preset.id;
        button.setAttribute('aria-label', `${preset.id}, ${preset.name}, contour ${Math.round(preset.contour * 100)} percent`);
        button.innerHTML = `<span class="motion-column__id">${preset.id}</span>`
            + `<b>${preset.name}</b><span>${Math.round(preset.contour * 100)}%</span>`
            + `<span>${Math.round(preset.halo * 100)}%</span>`;
        button.addEventListener('click', () => selectMotion(index));
        motionMatrix.append(button);
    });
    motionSlider.addEventListener('input', () => selectMotion(Number(motionSlider.value) - 1));
    document.querySelectorAll<HTMLButtonElement>('[data-idle-size]').forEach(button =>
        button.addEventListener('click', () => selectIdleSize(button.dataset.idleSize!)));

    VOICE_PALETTE_SYSTEMS.forEach((system, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'palette-card';
        button.dataset.paletteChoice = system.id;
        button.setAttribute('aria-label', `Palette ${index + 1}: ${system.name}`);
        const strip = (label: string, values: readonly string[]) =>
            `<span class="palette-strip"><small>${label}</small>${values.map(value =>
                `<i style="--swatch:${value}"></i>`).join('')}</span>`;
        button.innerHTML = `<span class="palette-card__index">${String(index + 1).padStart(2, '0')}</span>`
            + `<span class="palette-card__copy"><b>${system.name}</b><em>${system.character}</em></span>`
            + `<span class="palette-card__swatches">`
            + strip('F', [system.light.idle, system.light.recordFar, system.light.processCyan,
                system.light.sending, system.light.done, system.light.fail])
            + strip('D', [system.dark.idle, system.dark.recordFar, system.dark.processCyan,
                system.dark.sending, system.dark.done, system.dark.fail]) + '</span>';
        button.addEventListener('click', () => {
            currentPalette = system.id;
            document.documentElement.dataset.voicePalette = currentPalette;
            composer.setVoicePalette(currentPalette);
            sendingPreviews.forEach(orb => orb.setPalette(currentPalette));
            fibonacciPreviews.forEach(orb => orb.setPalette(currentPalette));
            document.querySelectorAll<HTMLButtonElement>('[data-palette-choice]').forEach(item =>
                item.setAttribute('aria-pressed', String(item.dataset.paletteChoice === currentPalette)));
            applyChromaticTokens();
            updateLocation();
        });
        paletteGrid.append(button);
    });
    paletteGrid.querySelectorAll<HTMLButtonElement>('[data-palette-choice]').forEach(item =>
        item.setAttribute('aria-pressed', String(item.dataset.paletteChoice === currentPalette)));
    applyChromaticTokens();

    composer = new VoiceComposer(host, {
        ariaLabel: 'Dirac message composer interaction preview',
        placeholder: 'Ask Dirac about this molecule…',
        driver: new InteractionDriver(),
        onSubmit: text => {
            toast.textContent = `Submitted · ${text.length} characters`;
            toast.dataset.visible = 'true';
            setTimeout(() => { toast.dataset.visible = 'false'; }, 2400);
        },
    });
    composer.setVoicePalette(currentPalette);
    composer.setVoiceSendingVariant(currentSending);
    composer.setVoiceSendingSizePreset(currentSendingSize);
    const initialMotionIndex = VOICE_IDLE_MOTION_PRESETS.findIndex(item => item.id === currentMotion);
    selectMotion(initialMotionIndex);
    selectIdleSize(currentIdleSize);
    composer.textarea.focus();
    let stateSignalTimer: ReturnType<typeof setInterval> | undefined;
    showDaemonState = (state: VoiceDaemonState) => {
        if (stateSignalTimer) clearInterval(stateSignalTimer);
        composer.setDaemonState(state);
        document.querySelectorAll<HTMLButtonElement>('[data-daemon-state]').forEach(button =>
            button.setAttribute('aria-pressed', String(button.dataset.daemonState === state)));
        if (state === 'recording' || state === 'calibrating') {
            let tick = 0;
            stateSignalTimer = setInterval(() => {
                tick++;
                const level = .16 + Math.abs(Math.sin(tick * .51)) * .78;
                composer.setDaemonState(state, { level,
                    tone: .20 + Math.abs(Math.sin(tick * .23)) * .72,
                    hearing: level > .26 });
            }, 100);
        }
    };

    const selectSending = (id: string) => {
        const variant = VOICE_SENDING_VARIANTS.find(item => item.id === id);
        if (!variant) return;
        currentSending = variant.id;
        composer.setVoiceSendingVariant(currentSending);
        sendingName.textContent = `${variant.id} · ${variant.name}`;
        const treatment = VOICE_SENDING_SIZE_PRESETS.find(item => item.id === currentSendingSize)!;
        sendingCharacter.textContent = variant.id === '16'
            ? `${treatment.target}px · ${treatment.name} · ${treatment.character}`
            : `${variant.equation} · ${variant.character}`;
        document.querySelectorAll<HTMLButtonElement>('[data-sending-choice]').forEach(button =>
            button.setAttribute('aria-pressed', String(button.dataset.sendingChoice === currentSending)));
        showDaemonState('sending');
        updateLocation();
    };

    const selectFibonacciTreatment = (id: string) => {
        const treatment = VOICE_SENDING_SIZE_PRESETS.find(item => item.id === id);
        if (!treatment) return;
        currentSending = '16';
        currentSendingSize = treatment.id;
        composer.setVoiceSendingVariant('16');
        composer.setVoiceSendingSizePreset(treatment.id);
        sendingPreviews.forEach(orb => orb.setSendingSizePreset(treatment.id));
        sendingName.textContent = '16 · Fibonacci Disk';
        sendingCharacter.textContent = `${treatment.target}px · ${treatment.name} · ${treatment.character}`;
        sendingSizeValue.textContent = String(treatment.target);
        document.querySelectorAll<HTMLButtonElement>('[data-fibonacci-treatment]').forEach(button =>
            button.setAttribute('aria-pressed', String(button.dataset.fibonacciTreatment === currentSendingSize)));
        document.querySelectorAll<HTMLButtonElement>('[data-sending-choice]').forEach(button =>
            button.setAttribute('aria-pressed', String(button.dataset.sendingChoice === '16')));
        showDaemonState('sending');
        updateLocation();
    };

    VOICE_SENDING_SIZE_PRESETS.forEach(treatment => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'fibonacci-option';
        button.dataset.fibonacciTreatment = treatment.id;
        button.setAttribute('aria-label', `${treatment.target} pixels, ${treatment.name}, ${treatment.character}`);
        const previewHost = document.createElement('span');
        previewHost.className = 'fibonacci-option__preview';
        const copy = document.createElement('span');
        copy.className = 'fibonacci-option__copy';
        copy.innerHTML = `<small>${treatment.target}px</small><b>${treatment.name}</b>`
            + `<em>${treatment.character}</em>`;
        button.append(previewHost, copy);
        const orb = new VoiceDaemonOrb(previewHost);
        orb.setPalette(currentPalette);
        orb.setSendingVariant('16');
        orb.setSendingSizePreset(treatment.id);
        orb.setState('sending');
        fibonacciPreviews.push(orb);
        button.addEventListener('click', () => selectFibonacciTreatment(treatment.id));
        fibonacciVariants.append(button);
    });

    if (!sendingGrid.hidden) VOICE_SENDING_VARIANTS.forEach(variant => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sending-card';
        button.dataset.sendingChoice = variant.id;
        button.setAttribute('aria-label', `${variant.id}, ${variant.name}. ${variant.equation}. ${variant.character}`);
        const previewHost = document.createElement('span');
        previewHost.className = 'sending-card__preview';
        const copy = document.createElement('span');
        copy.className = 'sending-card__copy';
        copy.innerHTML = `<small>${variant.id}</small><b>${variant.name}</b>`
            + `<code>${variant.equation}</code><em>${variant.character}</em>`;
        button.append(previewHost, copy);
        const orb = new VoiceDaemonOrb(previewHost);
        orb.setPalette(currentPalette);
        orb.setSendingVariant(variant.id);
        orb.setSendingSizePreset(currentSendingSize);
        orb.setState('sending');
        sendingPreviews.push(orb);
        button.addEventListener('click', () => selectSending(variant.id));
        sendingGrid.append(button);
    });
    const selectedSending = VOICE_SENDING_VARIANTS.find(item => item.id === currentSending)!;
    sendingName.textContent = `${selectedSending.id} · ${selectedSending.name}`;
    const selectedTreatment = VOICE_SENDING_SIZE_PRESETS.find(item => item.id === currentSendingSize)!;
    sendingCharacter.textContent = selectedSending.id === '16'
        ? `${selectedTreatment.target}px · ${selectedTreatment.name} · ${selectedTreatment.character}`
        : `${selectedSending.equation} · ${selectedSending.character}`;
    sendingSizeValue.textContent = String(selectedTreatment.target);
    document.querySelectorAll<HTMLButtonElement>('[data-fibonacci-treatment]').forEach(button =>
        button.setAttribute('aria-pressed', String(button.dataset.fibonacciTreatment === currentSendingSize)));
    document.querySelectorAll<HTMLButtonElement>('[data-sending-choice]').forEach(button =>
        button.setAttribute('aria-pressed', String(button.dataset.sendingChoice === currentSending)));
    document.querySelectorAll<HTMLButtonElement>('[data-daemon-state]').forEach(button =>
        button.addEventListener('click', () =>
            showDaemonState(button.dataset.daemonState as VoiceDaemonState)));

    const themeButtons = document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]');
    const applyTheme = (dark: boolean) => {
        currentDark = dark;
        if (dark) document.documentElement.setAttribute('data-theme', 'dark');
        else document.documentElement.removeAttribute('data-theme');
        themeButtons.forEach(item =>
            item.setAttribute('aria-pressed', String((item.dataset.themeChoice === 'dark') === dark)));
        applyChromaticTokens();
    };
    themeButtons.forEach(button => {
        button.addEventListener('click', () => {
            applyTheme(button.dataset.themeChoice === 'dark');
            updateLocation();
        });
    });
    applyTheme(currentDark);
    requestAnimationFrame(() => requestAnimationFrame(() =>
        document.documentElement.setAttribute('data-theme-ready', 'true')));
    const previewState = preview.get('state');
    if (previewState && ['idle', 'off', 'calibrating', 'recording', 'transcribing',
        'sending', 'done', 'no_speech', 'filtered', 'fail'].includes(previewState)) {
        showDaemonState(previewState as VoiceDaemonState);
    } else if (preview.has('state')) {
        const dictate = composer.root.querySelector('.voice-composer__dictate') as HTMLButtonElement;
        setTimeout(() => dictate.click(), 180);
    }
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();
