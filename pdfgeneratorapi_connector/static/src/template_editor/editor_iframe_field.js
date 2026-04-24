/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useEffect, useRef } from "@odoo/owl";

/**
 * Renders a Char field's value as the `src` of a plain (un-sandboxed) <iframe>.
 *
 * Needed because Odoo's widget="html" with sandboxedPreview=true wraps content
 * in a script-blocked sandbox (perfect for the HTML preview, fatal for a full
 * JS editor). Setting `src` imperatively also keeps the URL out of the DOM
 * attribute stream, so a stray character in the signed query can't break out.
 */
export class PdfgenEditorIframeField extends Component {
    static template = "pdfgeneratorapi_connector.PdfgenEditorIframeField";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.iframeRef = useRef("iframe");
        useEffect(
            (url) => {
                if (!this.iframeRef.el) {
                    return;
                }
                this.iframeRef.el.src = url || "about:blank";
            },
            () => [this.props.record.data[this.props.name]]
        );
    }
}

export const pdfgenEditorIframeField = {
    component: PdfgenEditorIframeField,
    supportedTypes: ["char"],
};

registry.category("fields").add("pdfgen_editor_iframe", pdfgenEditorIframeField);
