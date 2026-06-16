/** @odoo-module **/

import { useEffect, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import {
    DynamicModelFieldSelectorChar,
    dynamicModelFieldSelectorChar,
} from "@web/views/fields/dynamic_widget/dynamic_model_field_selector_char";

/**
 * Thin wrapper around the stock dynamic field selector that also accepts drops
 * from the pdfgen field palette. The standard keyboard / click-picker flow is
 * inherited from DynamicModelFieldSelectorChar untouched (getSelectorProps,
 * getResModel, filter, _onRecordUpdate); we only add the drag-to-bind
 * affordance on the wrapper element.
 *
 * Odoo 18: the field-selector field widget is DynamicModelFieldSelectorChar
 * (a CharField subclass rendering DynamicModelFieldSelector). Odoo 19
 * renamed/moved it to @web/views/fields/field_selector/field_selector_field
 * (FieldSelectorField); this module targets the 18 API.
 */
export class PdfgenDroppableFieldSelector extends DynamicModelFieldSelectorChar {
    static template = "pdfgeneratorapi_connector.DroppableFieldSelector";

    setup() {
        super.setup();
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
}

export const pdfgenDroppableFieldSelector = {
    ...dynamicModelFieldSelectorChar,
    component: PdfgenDroppableFieldSelector,
};

registry
    .category("fields")
    .add("pdfgen_droppable_field_selector", pdfgenDroppableFieldSelector);
