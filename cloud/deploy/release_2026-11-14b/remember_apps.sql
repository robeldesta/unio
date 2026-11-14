-- Release 12 Nov 2026 (a), step before the update: remember which apps this company has switched off.
-- The update reloads every app's menu as visible; the new code then re-applies this choice (saas.apps_off).
-- Written only if the company has no choice kept yet. ',' = nothing switched off.
INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
SELECT 'saas.apps_off', coalesce(string_agg(c.key, ',' ORDER BY c.key), ','), 1, 1, now(), now()
  FROM (VALUES
    ('accounting', 'sonador_books', 'menu_sb_root'),
    ('sales', 'sonador_sales', 'menu_sales_root'),
    ('crm', 'sonador_crm', 'menu_crm_root'),
    ('purchase', 'sonador_purchase', 'menu_purchase_root'),
    ('inventory', 'sonador_inventory', 'menu_store_root'),
    ('production', 'sonador_factory', 'menu_root'),
    ('projects', 'sonador_project', 'menu_root'),
    ('planning', 'sonador_planning', 'menu_planning_root'),
    ('people', 'sonador_books', 'menu_hr_root'),
    ('fleet', 'sonador_fleet', 'menu_root'),
    ('website', 'sonador_site', 'menu_site_root'),
    ('school', 'sonador_school', 'menu_school_root'),
    ('farm', 'sonador_farm', 'menu_root'),
    ('agenda', 'sonador_agenda', 'menu_root'),
    ('controller', 'sonador_controller', 'menu_root'),
    ('audit', 'sonador_audit', 'menu_root'),
    ('studio', 'sonador_studio', 'menu_studio_root')
  ) AS c(key, module, name)
  JOIN ir_model_data d ON d.module = c.module AND d.name = c.name AND d.model = 'ir.ui.menu'
  JOIN ir_ui_menu m ON m.id = d.res_id
 WHERE m.active = false
ON CONFLICT (key) DO NOTHING;
SELECT '> apps switched off: ' || coalesce((SELECT value FROM ir_config_parameter WHERE key = 'saas.apps_off'), '(none kept)');
