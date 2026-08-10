/**
 * A deliberately UI-framework-free Mol* example.
 *
 * The application owns every visible control. Mol* supplies only the plugin
 * engine, state tree, data builders, and Canvas3D renderer.
 */

import { Vec3 } from '../../mol-math/linear-algebra';
import { ChemWorkbench, createChemWorkbench } from '../../chemistry.backend.perception.rdkit-wasm.editable';
import { allChemPacks } from '../../chemistry.backend.perception.rdkit-wasm.editable/presets';
import { PluginCommands } from '../../mol-plugin/commands';
import { Color } from '../../mol-util/color';
import './index.html';

function byId<T extends HTMLElement>(id: string): T {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing element #${id}`);
    return element as T;
}

class KernelInterface {
    private workbench?: ChemWorkbench;

    private readonly app = byId<HTMLElement>('app');
    private readonly status = byId<HTMLElement>('status');
    private readonly structureName = byId<HTMLElement>('structure-name');
    private readonly structureStats = byId<HTMLElement>('structure-stats');
    private isDark = false;

    get plugin() {
        if (!this.workbench) throw new Error('Chemistry workbench is not initialized');
        return this.workbench.plugin;
    }

    async init() {
        this.setStatus('Starting Mol* engine…', 'busy');
        this.workbench = await createChemWorkbench({
            target: byId<HTMLElement>('viewport'),
            packs: allChemPacks,
        });
        this.app.dataset.packs = this.workbench.manifest.packs.join(',');

        this.bindControls();
        this.plugin.behaviors.state.isBusy.subscribe(isBusy => {
            this.app.dataset.engineBusy = String(isBusy);
        });
        window.addEventListener('beforeunload', () => this.workbench?.dispose(), { once: true });

        await this.loadBundledStructure();
    }

    private bindControls() {
        byId<HTMLButtonElement>('load-example').addEventListener('click', () => {
            void this.loadBundledStructure();
        });
        byId<HTMLButtonElement>('reset-camera').addEventListener('click', () => {
            void this.workbench?.resetCamera();
        });
        byId<HTMLButtonElement>('toggle-spin').addEventListener('click', () => this.toggleSpin());
        byId<HTMLButtonElement>('toggle-background').addEventListener('click', () => this.toggleBackground());
        byId<HTMLInputElement>('open-file').addEventListener('change', event => {
            const file = (event.currentTarget as HTMLInputElement).files?.[0];
            if (file) void this.loadFile(file);
        });
    }

    private async loadBundledStructure() {
        const url = new URL('../../../examples/1crn.cif', window.location.href);
        this.setStatus('Loading bundled 1CRN…', 'busy');
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            await this.loadText(await response.text(), '1crn.cif');
        } catch (error) {
            this.fail(error);
        }
    }

    private async loadFile(file: File) {
        this.setStatus(`Reading ${file.name}…`, 'busy');
        try {
            await this.loadText(await file.text(), file.name);
        } catch (error) {
            this.fail(error);
        }
    }

    private async loadText(data: string, label: string) {
        await this.workbench?.loadStructureFromData(data, { format: 'mmcif', label });

        const structures = this.plugin.managers.structure.hierarchy.current.structures;
        const elementCount = structures.reduce((sum, item) => sum + (item.cell.obj?.data.elementCount ?? 0), 0);
        this.structureName.textContent = label;
        this.structureStats.textContent = `${structures.length} structure · ${elementCount.toLocaleString()} elements`;
        this.app.dataset.ready = 'true';
        this.setStatus('Ready', 'ready');
    }

    private toggleSpin() {
        const canvas = this.plugin.canvas3d;
        if (!canvas) return;

        const trackball = canvas.props.trackball;
        const isSpinning = trackball.animate.name === 'spin';
        void PluginCommands.Canvas3D.SetSettings(this.plugin, {
            settings: {
                trackball: {
                    ...trackball,
                    animate: isSpinning
                        ? { name: 'off', params: {} }
                        : { name: 'spin', params: { speed: 0.8, axis: Vec3.create(0, 1, 0) } }
                }
            }
        });

        const button = byId<HTMLButtonElement>('toggle-spin');
        button.dataset.active = String(!isSpinning);
        button.textContent = isSpinning ? 'Start spin' : 'Stop spin';
    }

    private toggleBackground() {
        this.isDark = !this.isDark;
        const backgroundColor = Color(this.isDark ? 0x11161D : 0xF4F1EA);
        void this.workbench?.setBackground(backgroundColor);
        this.app.dataset.darkViewport = String(this.isDark);
        byId<HTMLButtonElement>('toggle-background').textContent = this.isDark
            ? 'Light background'
            : 'Dark background';
    }

    private setStatus(message: string, state: 'busy' | 'ready' | 'error') {
        this.status.textContent = message;
        this.status.dataset.state = state;
    }

    fail(error: unknown) {
        console.error(error);
        this.app.dataset.ready = 'false';
        this.setStatus(error instanceof Error ? error.message : String(error), 'error');
    }
}

const kernelInterface = new KernelInterface();
(window as any).kernelInterface = kernelInterface;
void kernelInterface.init().catch(error => kernelInterface.fail(error));
