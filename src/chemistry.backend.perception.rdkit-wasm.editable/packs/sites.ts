import { ANVILMembraneOrientation } from '../../extensions/anvil/behavior';
import { AssemblySymmetryClusterColorThemeProvider } from '../../extensions/assembly-symmetry/color';
import { AssemblySymmetryProvider } from '../../extensions/assembly-symmetry/prop';
import { SbNcbrTunnels } from '../../extensions/sb-ncbr/tunnels/behavior';
import { PluginBehavior } from '../../mol-plugin/behavior/behavior';
import { PluginSpec } from '../../mol-plugin/spec';
import { ParamDefinition as PD } from '../../mol-util/param-definition';
import { defineChemPack } from '../types';
import { corePack } from './core';

/** Assembly-symmetry semantics without the extension's React control. */
const HeadlessAssemblySymmetry = PluginBehavior.create<{ autoAttach: boolean }>({
    name: 'chem-assembly-symmetry',
    category: 'custom-props',
    display: { name: 'Assembly Symmetry', description: 'Assembly symmetry property and color theme without UI controls.' },
    ctor: class extends PluginBehavior.Handler<{ autoAttach: boolean }> {
        register() {
            this.ctx.customStructureProperties.register(AssemblySymmetryProvider, this.params.autoAttach);
            this.ctx.representation.structure.themes.colorThemeRegistry.add(AssemblySymmetryClusterColorThemeProvider);
        }

        update(params: { autoAttach: boolean }) {
            const changed = this.params.autoAttach !== params.autoAttach;
            this.params.autoAttach = params.autoAttach;
            this.ctx.customStructureProperties.setDefaultAutoAttach(AssemblySymmetryProvider.descriptor.name, params.autoAttach);
            return changed;
        }

        unregister() {
            this.ctx.customStructureProperties.unregister(AssemblySymmetryProvider.descriptor.name);
            this.ctx.representation.structure.themes.colorThemeRegistry.remove(AssemblySymmetryClusterColorThemeProvider);
        }
    },
    params: () => ({ autoAttach: PD.Boolean(false) }),
});

export const sitesPack = defineChemPack({
    id: 'sites',
    label: 'Sites, Tunnels, and Membranes',
    description: 'ChannelsDB tunnels, ANVIL membrane orientation, and headless assembly-symmetry annotations.',
    dependencies: [corePack],
    capabilities: ['site.tunnels', 'site.membrane-orientation', 'site.assembly-symmetry'],
    spec: {
        behaviors: [
            PluginSpec.Behavior(SbNcbrTunnels),
            PluginSpec.Behavior(ANVILMembraneOrientation),
            PluginSpec.Behavior(HeadlessAssemblySymmetry),
        ],
    },
});

export { AssemblySymmetryProvider };
export { MembraneOrientationProvider } from '../../extensions/anvil/prop';
export { TunnelsFromRawData, TunnelFromRawData, TunnelShapeProvider } from '../../extensions/sb-ncbr/tunnels/representation';
