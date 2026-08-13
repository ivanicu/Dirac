declare module 'frappe-gantt' {
    export interface GanttTask {
        id: string;
        name: string;
        start: string;
        end: string;
        progress?: number;
        dependencies?: string;
        custom_class?: string;
        description?: string;
        [key: string]: unknown;
    }

    export interface GanttOptions {
        view_mode?: string;
        view_modes?: readonly string[];
        view_mode_select?: boolean;
        readonly?: boolean;
        readonly_dates?: boolean;
        readonly_progress?: boolean;
        popup?: false | ((context: unknown) => void);
        scroll_to?: string | null;
        infinite_padding?: boolean;
        language?: string;
        on_click?: (task: GanttTask) => void;
        on_view_change?: (mode: unknown) => void;
        [key: string]: unknown;
    }

    export default class Gantt {
        constructor(wrapper: HTMLElement | SVGElement | string,
            tasks: readonly GanttTask[], options?: GanttOptions);
        refresh(tasks: readonly GanttTask[]): void;
        change_view_mode(mode?: string, maintainPosition?: boolean): void;
        scroll_current(): void;
    }
}
