/** @odoo-module **/

import { Component, useEffect, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import {
    FieldSelectorField,
    fieldSelectorField,
} from "@web/views/fields/field_selector/field_selector_field";

/**
 * Thin wrapper around the stock field_selector that also accepts drops from
 * the pdfgen field palette. Standard keyboard / click-picker flow is
 * delegated to FieldSelectorField untouched; we only add the drag-to-bind
 * affordance on the wrapper element.
 */
export class PdfgenDroppableFieldSelector extends Component {
    static template = "pdfgeneratorapi_connector.DroppableFieldSelector";
    static components = { FieldSelectorField };
    static props = {
        ...standardFieldProps,
        resModel: { type: String, optional: true },
        onlySearchable: { type: Boolean, optional: true },
        allowProperties: { type: Boolean, optional: true },
        followRelations: { type: Boolean, optional: true },
    };

    setup() {
        this.rootRef = useRef("root");
        // The palette fires a custom `pdfgen-field-drop` event on us when the
        // user drops a field onto our wrapper. The palette handles the HTML5
        // drag/drop plumbing at the document level so list-editor
        // stopPropagation doesn't break the flow.
        useEffect(
            (el) => {
                if (!el) {
                    return;
                }
                const handler = (ev) => this.onFieldDrop(ev);
                el.addEventListener("pdfgen-field-drop", handler);
                return () => el.removeEventListener("pdfgen-field-drop", handler);
            },
            () => [this.rootRef.el]
        );
    }

    async onFieldDrop(ev) {
        if (this.props.readonly) {
            return;
        }
        const path = ev.detail && ev.detail.path;
        if (!path) {
            return;
        }
        await this.props.record.update({ [this.props.name]: path });
    }

    get innerProps() {
        // Forward every standard prop + our picker-specific props; the inner
        // FieldSelectorField takes over from there.
        const {
            resModel,
            onlySearchable,
            allowProperties,
            followRelations,
            ...rest
        } = this.props;
        return {
            ...rest,
            resModel,
            onlySearchable,
            allowProperties,
            followRelations,
        };
    }

}

export const pdfgenDroppableFieldSelector = {
    ...fieldSelectorField,
    component: PdfgenDroppableFieldSelector,
};

registry
    .category("fields")
    .add("pdfgen_droppable_field_selector", pdfgenDroppableFieldSelector);
