/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Component, useEffect, useRef } from "@odoo/owl";

/**
 * Renders a Char field's value as the `src` of a plain (un-sandboxed) <iframe>,
 * and listens for `window.message` events from the embedded editor so Odoo can
 * react to save / close events.
 *
 * Needed because Odoo's widget="html" with sandboxedPreview=true wraps content
 * in a script-blocked sandbox (perfect for the HTML preview, fatal for a full
 * JS editor). Setting `src` imperatively also keeps the URL out of the DOM
 * attribute stream, so a stray character in the signed query can't break out.
 *
 * Security: every incoming `message` is dropped unless its `origin` matches
 * the iframe's current src origin. Without this check any page embedded in
 * the same browser could forge a "save" event and trigger our handlers.
 *
 * Event shape: pdfgeneratorapi.com's exact postMessage payload isn't fully
 * documented — we treat messages with a `type` (or `event`) string property,
 * case-insensitively matched against `/save/` or `/close/`. Unknown messages
 * land in console.debug so the shape can be discovered in practice.
 */
export class PdfgenEditorIframeField extends Component {
    static template = "pdfgeneratorapi_connector.PdfgenEditorIframeField";
    static props = { ...standardFieldProps };

    setup() {
        this.iframeRef = useRef("iframe");
        this.notificationService = useService("notification");

        useEffect(
            (url) => {
                if (!this.iframeRef.el) {
                    return;
                }
                this.iframeRef.el.src = url || "about:blank";
                if (!url) {
                    return;
                }
                // Hand focus to the iframe once it finishes loading so
                // arrow keys / Delete / typing reach the editor without
                // the user having to click into it first. Without this
                // the parent (Odoo) keeps focus and keyboard events are
                // silently swallowed until the user clicks an editor
                // input — the exact symptom users reported.
                const onLoad = () => {
                    try {
                        this.iframeRef.el.contentWindow?.focus();
                    } catch {
                        // Cross-origin contentWindow.focus() is generally
                        // allowed but defensively swallow any browser-
                        // specific quirks so this never breaks Open.
                    }
                };
                this.iframeRef.el.addEventListener("load", onLoad, { once: true });
            },
            () => [this.props.record.data[this.props.name]]
        );

        // Register the message listener once on mount and unregister on
        // unmount — prevents leaks if the user navigates away between edits.
        const onMessage = this._onMessage.bind(this);
        useEffect(
            () => {
                window.addEventListener("message", onMessage);
                return () => {
                    window.removeEventListener("message", onMessage);
                };
            },
            () => []
        );
    }

    get expectedOrigin() {
        const url = this.props.record.data[this.props.name];
        if (!url) {
            return null;
        }
        try {
            return new URL(url).origin;
        } catch {
            return null;
        }
    }

    _onMessage(event) {
        const expected = this.expectedOrigin;
        if (!expected || event.origin !== expected) {
            return;
        }
        const data = event.data;
        const type =
            (data && typeof data === "object" && (data.type || data.event)) ||
            (typeof data === "string" ? data : null);
        if (!type) {
            return;
        }
        // Log everything so the exact pdfgen event shape can be discovered
        // in production if it changes or surfaces new events.
        console.debug("[pdfgen-editor] message:", type, data);

        // Match a strict whitelist. The previous `.includes("save"/"close")`
        // matched event names like `closePanel`, `componentClosed`, etc.,
        // which the editor emits on internal UI interactions (tab switches,
        // panel toggles). False-positive matches cleared `editor_url` and
        // unloaded the iframe — triggering the editor's own beforeunload
        // ("Leave site? Changes you made may not be saved.") on every
        // panel click. Whitelist exact tokens only; everything else is
        // logged for discovery and ignored.
        const normalized = String(type).toLowerCase().trim();
        const SAVE_EVENTS = new Set(["save", "saved", "template.save", "template:save"]);
        const CLOSE_EVENTS = new Set(["close", "closed", "editor.close", "editor:close"]);
        if (SAVE_EVENTS.has(normalized)) {
            this.notificationService.add(_t("Template saved."), {
                type: "success",
                sticky: false,
            });
        } else if (CLOSE_EVENTS.has(normalized)) {
            this.notificationService.add(_t("Editor closed."), {
                type: "info",
                sticky: false,
            });
            // Clear the field so the iframe collapses — reflects that the
            // session is done. User can click Open again to mint a fresh URL.
            this.props.record.update({ [this.props.name]: false });
        }
    }
}

export const pdfgenEditorIframeField = {
    component: PdfgenEditorIframeField,
    supportedTypes: ["char"],
};

registry.category("fields").add("pdfgen_editor_iframe", pdfgenEditorIframeField);
