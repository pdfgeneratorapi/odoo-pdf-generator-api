/** @odoo-module **/

import { Component, onWillStart, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// MIME used when dragging a field path between the palette and a drop-accepting
// field input. Kept in sync with droppable_field_selector.js.
export const PDFGEN_FIELD_MIME = "application/x-pdfgen-field-path";

/**
 * Palette that browses ir.model.fields for a given Odoo model and lets the
 * user drag any field (including dotted paths via relation drill-down) onto a
 * drop-accepting input elsewhere in the form.
 *
 * Rendered via the <widget name="pdfgen_field_palette"/> tag on the mapping
 * form. Reads the current model from the mapping's `model` field on the
 * record so it stays in sync if the user changes it.
 */
export class PdfgenFieldPalette extends Component {
    static template = "pdfgeneratorapi_connector.FieldPalette";
    static props = { record: Object, readonly: { type: Boolean, optional: true } };

    setup() {
        this.fieldService = useService("field");
        this.state = useState({
            stack: [], // breadcrumb of { model, label, pathSegment }
            fields: [],
            search: "",
            loading: false,
        });
        onWillStart(async () => {
            if (this.rootModel) {
                await this.loadModel(this.rootModel, "", "");
            }
        });
        // Reload when the mapping's model field changes (e.g. user picks a
        // different Odoo Model in the top half of the form).
        useEffect(
            (model) => {
                if (!model) {
                    return;
                }
                if (this.state.stack.length === 0 || this.state.stack[0].model !== model) {
                    this.state.stack = [];
                    this.loadModel(model, "", "");
                }
            },
            () => [this.rootModel]
        );
    }

    get rootModel() {
        return this.props.record.data.model || "";
    }

    get currentModel() {
        return this.state.stack.length
            ? this.state.stack[this.state.stack.length - 1].model
            : this.rootModel;
    }

    get currentPath() {
        return this.state.stack
            .map((frame) => frame.pathSegment)
            .filter(Boolean)
            .join(".");
    }

    get breadcrumb() {
        // Root crumb is the mapping's model; subsequent crumbs are each drill-down.
        return [
            { label: this.rootModel, pathSegment: "", model: this.rootModel },
            ...this.state.stack.slice(1),
        ];
    }

    get filteredFields() {
        const q = this.state.search.trim().toLowerCase();
        if (!q) {
            return this.state.fields;
        }
        return this.state.fields.filter((f) => {
            return (
                f.name.toLowerCase().includes(q) ||
                (f.string || "").toLowerCase().includes(q)
            );
        });
    }

    async loadModel(model, label, pathSegment) {
        this.state.loading = true;
        try {
            const fieldDefs = await this.fieldService.loadFields(model, {
                attributes: ["string", "type", "relation", "searchable"],
            });
            const rows = [];
            for (const [name, def] of Object.entries(fieldDefs || {})) {
                rows.push({
                    name,
                    string: def.string || name,
                    ttype: def.type,
                    relation: def.relation,
                    isRelation: ["many2one", "one2many", "many2many"].includes(def.type),
                });
            }
            rows.sort((a, b) => a.string.localeCompare(b.string));
            this.state.fields = rows;
            if (this.state.stack.length === 0) {
                this.state.stack.push({ model, label: model, pathSegment: "" });
            } else {
                this.state.stack.push({ model, label, pathSegment });
            }
        } catch (err) {
            console.error("pdfgen field palette: loadModel failed for", model, err);
            this.state.fields = [];
        } finally {
            this.state.loading = false;
        }
    }

    async onDrillIn(field) {
        if (!field.isRelation || !field.relation) {
            return;
        }
        await this.loadModel(field.relation, field.string, field.name);
    }

    async onBreadcrumbClick(index) {
        if (index >= this.state.stack.length - 1) {
            return;
        }
        // Rewind to that frame and re-load it.
        const frame = this.state.stack[index];
        this.state.stack = this.state.stack.slice(0, index);
        await this.loadModel(frame.model, frame.label, frame.pathSegment);
    }

    onPointerDown(event, field) {
        if (this.props.readonly) {
            return;
        }
        // Only left button.
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        const base = this.currentPath;
        const path = base ? `${base}.${field.name}` : field.name;
        const startX = event.clientX;
        const startY = event.clientY;
        let ghost = null;
        let currentTarget = null;

        const createGhost = () => {
            const el = document.createElement("div");
            el.className = "o_pdfgen_drag_ghost";
            el.textContent = field.string + " (" + field.name + ")";
            document.body.appendChild(el);
            return el;
        };

        const onMove = (ev) => {
            if (!ghost) {
                if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < 4) {
                    return;
                }
                ghost = createGhost();
                document.body.classList.add("o_pdfgen_dragging");
            }
            ghost.style.left = `${ev.clientX + 12}px`;
            ghost.style.top = `${ev.clientY + 12}px`;
            // Highlight the row under the cursor so the user sees where the
            // drop will land, regardless of whether that row is in edit mode.
            const el = document.elementFromPoint(ev.clientX, ev.clientY);
            const row = el && el.closest ? el.closest("tr.o_data_row") : null;
            if (row !== currentTarget) {
                if (currentTarget) {
                    currentTarget.classList.remove("o_pdfgen_drop_row");
                }
                if (row) {
                    row.classList.add("o_pdfgen_drop_row");
                }
                currentTarget = row;
            }
        };

        const onUp = async (ev) => {
            window.removeEventListener("pointermove", onMove, true);
            window.removeEventListener("pointerup", onUp, true);
            document.body.classList.remove("o_pdfgen_dragging");
            if (ghost) {
                ghost.remove();
            }
            if (currentTarget) {
                currentTarget.classList.remove("o_pdfgen_drop_row");
            }
            // Find the row under the cursor at release time; rows carry a
            // `data-pdfgen-index` hook we set via view decoration.
            const el = document.elementFromPoint(ev.clientX, ev.clientY);
            const row = el && el.closest ? el.closest("tr.o_data_row") : null;
            if (!row) {
                return;
            }
            // Index among visible siblings in the same tbody.
            const tbody = row.parentElement;
            if (!tbody) {
                return;
            }
            const rows = Array.from(tbody.querySelectorAll(":scope > tr.o_data_row"));
            const index = rows.indexOf(row);
            if (index < 0) {
                return;
            }
            const lineRecords = this.props.record.data.line_ids &&
                this.props.record.data.line_ids.records;
            if (!lineRecords || !lineRecords[index]) {
                return;
            }
            const line = lineRecords[index];
            const currentExpr = line.data.expression || "";
            const currentPath = line.data.odoo_field_path || "";
            if (currentExpr) {
                // Already composing — append the new token.
                await line.update({ expression: `${currentExpr} {${path}}` });
            } else if (currentPath) {
                // Promote the existing bare path into an expression.
                await line.update({
                    expression: `{${currentPath}} {${path}}`,
                    odoo_field_path: false,
                });
            } else {
                await line.update({ odoo_field_path: path });
            }
        };

        window.addEventListener("pointermove", onMove, true);
        window.addEventListener("pointerup", onUp, true);
    }
}

export const pdfgenFieldPaletteWidget = {
    component: PdfgenFieldPalette,
    extractProps: ({ attrs }) => ({ readonly: attrs.readonly === "1" }),
};

registry.category("view_widgets").add("pdfgen_field_palette", pdfgenFieldPaletteWidget);
