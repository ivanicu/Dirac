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
        popup?: false | ((context: {
            task: GanttTask;
            chart: Gantt;
            set_title(value: string): void;
            set_subtitle(value: string): void;
            set_details(value: string): void;
            add_action(label: string, action: () => void): void;
        }) => void);
        popup_on?: 'click' | 'hover';
        show_expected_progress?: boolean;
        auto_move_label?: boolean;
        move_dependencies?: boolean;
        today_button?: boolean;
        scroll_to?: string | null;
        infinite_padding?: boolean;
        language?: string;
        on_click?: (task: GanttTask) => void;
        on_double_click?: (task: GanttTask) => void;
        on_date_change?: (task: GanttTask, start: Date, end: Date) => void;
        on_progress_change?: (task: GanttTask, progress: number) => void;
        on_view_change?: (mode: unknown) => void;
        [key: string]: unknown;
    }

    export default class Gantt {
        constructor(wrapper: HTMLElement | SVGElement | string,
            tasks: readonly GanttTask[], options?: GanttOptions);
        refresh(tasks: readonly GanttTask[]): void;
        change_view_mode(mode?: string, maintainPosition?: boolean): void;
        scroll_current(): void;
        update_options(options: GanttOptions): void;
        update_task(taskId: string, details: Partial<GanttTask>): void;
    }
}
