import { CENTERS } from "@sonador_books/nav_center/nav_center";
import { FEATURES, REPORT_TITLES } from "@sonador_books/report/report_viewer";

const M = (x) => `sonador_mrp.${x}`;

// Production sits in the Inventory & Services flow: recipes -> build -> finished goods
CENTERS.inventory.setup.splice(2, 0, { label: "Recipes (BOM)", icon: "fa-sitemap", action: M("action_sb_recipes"), hint: "with versions" });
CENTERS.inventory.flows.splice(2, 0, [
    { label: "Build (Produce)", icon: "fa-industry", action: M("action_sb_build"), create: true, hint: "mix, fill, assemble" },
    { label: "Cost Sheet", icon: "fa-calculator", report: "cost_sheet", hint: "cost per unit" },
]);
CENTERS.inventory.defaults = ["inv_valuation", "production_register", "cost_sheet", "stock_movement", "cogs_journal"];

Object.assign(FEATURES, { recipe_list: { asOf: true } });
Object.assign(REPORT_TITLES, {
    production_register: "Production Register",
    material_usage: "Material Usage vs Recipe",
    cost_sheet: "Cost Sheet",
    recipe_list: "Recipes (Bills of Materials)",
});
