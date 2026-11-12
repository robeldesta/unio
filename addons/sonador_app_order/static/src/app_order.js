import { patch } from '@web/core/utils/patch';
import { useRef } from '@odoo/owl';
import { user } from '@web/core/user';
import { useSortable } from '@web/core/utils/sortable_owl';
import { AppsBar } from '@muk_web_appsbar/webclient/appsbar/appsbar';
import { appMenuService } from '@muk_web_appsbar/webclient/menus/app_menu_service';

// Me, then Dashboard, Discuss and Agenda stay at the top for everyone; the rest follow each person's order
const PINNED = ['sonador_me.menu_me_root', 'sonador_dashboard.menu_dashboard_root', 'mail.menu_root_discuss', 'sonador_agenda.menu_root'];

function pinFirst(apps) {
    const head = PINNED.map((x) => apps.find((a) => a.xmlid === x)).filter(Boolean);
    return [...head, ...apps.filter((a) => !PINNED.includes(a.xmlid))];
}

patch(appMenuService, {
    async start(env, deps) {
        const service = await super.start(env, deps);
        const getApps = service.getAppsMenuItems.bind(service);
        service.getAppsMenuItems = () => pinFirst(getApps());
        return service;
    },
});

/** Drag an app in the left bar to put it where you want it; the order is kept per person and the apps grid follows. */
patch(AppsBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.appOrderRef = useRef('appOrder');
        useSortable({
            ref: this.appOrderRef,
            elements: 'li',
            touchDelay: 300,  // on a phone: press and hold, then drag (a tap still opens the app)
            tolerance: 10,  // with a mouse: a click opens the app, a drag of 10 px moves it
            preventDrag: (el) => PINNED.includes(el.querySelector('a')?.dataset.menuXmlid),
            onDrop: (params) => this._sbSaveOrder(params),
        });
    },
    async _sbSaveOrder({ element, previous }) {
        const order = this.appMenuService.getAppsMenuItems().map((a) => a.xmlid).filter((x) => !PINNED.includes(x));
        const moved = element.querySelector('a').dataset.menuXmlid;
        const rest = order.filter((x) => x !== moved);
        let after = previous ? previous.querySelector('a').dataset.menuXmlid : null;
        if (PINNED.includes(after)) {
            after = null;  // dropped among Me / Discuss: first after them
        }
        rest.splice(after ? rest.indexOf(after) + 1 : 0, 0, moved);
        const config = JSON.stringify(rest);
        user.updateUserSettings('homemenu_config', config);  // shown at once, then kept
        this.render(true);
        await user.setUserSettings('homemenu_config', config);
    },
});
