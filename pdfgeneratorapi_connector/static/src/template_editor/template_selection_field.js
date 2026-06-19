/** @odoo-module **/
// Odoo 17 doesn't auto-detect ES modules from `import`/`export` (18+ does),
// so the explicit module header is required here.

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { selectionField, SelectionField } from "@web/views/fields/selection/selection_field";

const LIBRARY_PREFIX = "lib:";
const NEW_TEMPLATE_VALUE = "__new__";

/**
 * Selection field that splits template options into dropdown sections:
 * an optional label-less group for the "+ Create new template…" magic entry,
 * "Default Templates" (public Template Library, values prefixed `lib:`) and
 * "My Templates" (the account's own templates). The grouping key is the
 * value shape produced by `pdfgen_template_selection()` on the Python side.
 */
export class PdfgenTemplateSelectionField extends SelectionField {
    static template = "pdfgeneratorapi_connector.PdfgenTemplateSelectionField";

    get groups() {
        const create = [];
        const library = [];
        const own = [];
        for (const [value, label] of this.options) {
            const choice = { value, label };
            if (value === NEW_TEMPLATE_VALUE) {
                create.push(choice);
            } else if (typeof value === "string" && value.startsWith(LIBRARY_PREFIX)) {
                library.push(choice);
            } else {
                own.push(choice);
            }
        }
        const groups = [];
        if (create.length) {
            groups.push({ choices: create });
        }
        if (library.length) {
            groups.push({ label: _t("Default Templates"), choices: library });
        }
        if (own.length) {
            groups.push({ label: _t("My Templates"), choices: own });
        }
        return groups;
    }

    get string() {
        // The base getter crashes when the stored value is missing from the
        // options (e.g. a saved library default while the library endpoint
        // is unreachable) — fall back to the raw value instead.
        if (this.type === "selection") {
            const raw = this.props.record.data[this.props.name];
            if (raw === false) {
                return "";
            }
            const option = this.options.find((o) => o[0] === raw);
            return option ? option[1] : String(raw);
        }
        return super.string;
    }
}

export const pdfgenTemplateSelectionField = {
    ...selectionField,
    component: PdfgenTemplateSelectionField,
};

registry.category("fields").add("pdfgen_template_selection", pdfgenTemplateSelectionField);
