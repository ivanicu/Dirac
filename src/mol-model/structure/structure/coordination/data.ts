/**
 * Copyright (c) 2026 mol* contributors, licensed under MIT, See LICENSE file for more info.
 *
 * @author Alexander Rose <alexander.rose@weirdbyte.de>
 */

import { Unit } from '../unit';
import { ElementIndex } from '../../model/indexing';
import { StructureElement } from '../element';

export type CoordinationIndex = { readonly '@type': 'coordination-index' } & number

export interface Coordination {
    readonly sites: {
        readonly unitIds: ReadonlyArray<number>
        readonly indices: ReadonlyArray<StructureElement.UnitIndex>
        readonly numbers: ReadonlyArray<number>
        readonly count: number
    }
    readonly getSiteIndex: (unit: Unit.Atomic, element: ElementIndex) => CoordinationIndex | -1
    readonly eachLigand: (siteIndex: CoordinationIndex, cb: (l: StructureElement.Location) => void) => void
}

export const EmptyCoordination: Coordination = {
    sites: {
        unitIds: [],
        indices: [],
        numbers: [],
        count: 0
    },
    getSiteIndex: () => -1,
    eachLigand: () => {}
};
