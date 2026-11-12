import { patch } from '@web/core/utils/patch';
import { useService } from '@web/core/utils/hooks';
import { useHotkey } from '@web/core/hotkeys/hotkey_hook';
import { user } from '@web/core/user';
import { isMacOS } from '@web/core/browser/feature_detection';
import { AppsBar } from '@muk_web_appsbar/webclient/appsbar/appsbar';

import { useState } from '@odoo/owl';

const TYPES = ['large', 'small'];

/** Collapse the apps sidebar to icons and back; the choice is saved on the user. */
patch(AppsBar.prototype, {
    setup() {
        super.setup();
        this.orm = useService('orm');
        const cls = [...document.body.classList].find((c) => c.startsWith('mk_sidebar_type_'));
        this.sbSidebar = useState({ type: cls ? cls.replace('mk_sidebar_type_', '') : 'large' });
        useHotkey('alt+b', () => this.sbToggleSidebar(), { global: true });
    },
    get sbShortcut() {
        // Odoo's hotkey modifier is Alt, or Control on a Mac
        return isMacOS() ? 'Ctrl+B' : 'Alt+B';
    },
    get sbCollapsed() {
        return this.sbSidebar.type === 'small';
    },
    sbToggleSidebar() {
        const next = this.sbCollapsed ? 'large' : 'small';
        document.body.classList.remove(...TYPES.map((t) => `mk_sidebar_type_${t}`));
        document.body.classList.add(`mk_sidebar_type_${next}`);
        this.sbSidebar.type = next;
        this.orm.write('res.users', [user.userId], { sidebar_type: next });
    },
});
