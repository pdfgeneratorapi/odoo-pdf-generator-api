/** @odoo-module **/

import { describe, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";

import {
    makeMockEnv,
    mockService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";

import { PdfgenEditorIframeField } from "@pdfgeneratorapi_connector/template_editor/editor_iframe_field";

function mockRecord(initialUrl = "") {
    const updateCalls = [];
    const record = {
        data: { editor_url: initialUrl },
        async update(vals) {
            updateCalls.push(vals);
            Object.assign(record.data, vals);
        },
    };
    record.updateCalls = updateCalls;
    return record;
}

async function mountWidget(url = "https://editor.pdfgeneratorapi.com/editor/42?token=abc") {
    const notifications = [];
    mockService("notification", () => ({
        add(message, options = {}) {
            notifications.push({ message, options });
        },
    }));
    await makeMockEnv();
    const record = mockRecord(url);
    await mountWithCleanup(PdfgenEditorIframeField, {
        props: {
            name: "editor_url",
            record,
            readonly: true,
        },
    });
    await animationFrame();
    return { record, notifications };
}

function postMessageFrom(origin, data) {
    window.dispatchEvent(
        new MessageEvent("message", { origin, data, source: window })
    );
}

describe("PdfgenEditorIframeField", () => {
    test("sets iframe.src to the field value on mount", async () => {
        await mountWidget("https://editor.pdfgeneratorapi.com/editor/1?token=x");
        const iframe = document.querySelector(".o_pdfgen_editor_iframe");
        expect(iframe).not.toBe(null);
        expect(iframe.src).toBe(
            "https://editor.pdfgeneratorapi.com/editor/1?token=x"
        );
    });

    test("dispatches save-type messages from the matching origin", async () => {
        const { notifications } = await mountWidget(
            "https://editor.pdfgeneratorapi.com/editor/1?token=x"
        );
        postMessageFrom("https://editor.pdfgeneratorapi.com", {
            type: "templateSaved",
            template: { id: 1 },
        });
        await animationFrame();
        expect(notifications.length).toBe(1);
        expect(notifications[0].message).toBe("Template saved.");
        expect(notifications[0].options.type).toBe("success");
    });

    test("ignores messages from a different origin", async () => {
        const { notifications } = await mountWidget(
            "https://editor.pdfgeneratorapi.com/editor/1?token=x"
        );
        postMessageFrom("https://evil.test", { type: "templateSaved" });
        await animationFrame();
        expect(notifications.length).toBe(0);
    });

    test("close-type event clears editor_url and notifies", async () => {
        const { record, notifications } = await mountWidget(
            "https://editor.pdfgeneratorapi.com/editor/1?token=x"
        );
        postMessageFrom("https://editor.pdfgeneratorapi.com", {
            type: "editorClosed",
        });
        await animationFrame();
        expect(notifications.length).toBe(1);
        expect(notifications[0].options.type).toBe("info");
        expect(record.updateCalls).toEqual([{ editor_url: false }]);
    });

    test("drops messages without a type / event string", async () => {
        const { notifications } = await mountWidget();
        postMessageFrom("https://editor.pdfgeneratorapi.com", { foo: "bar" });
        postMessageFrom("https://editor.pdfgeneratorapi.com", 42);
        await animationFrame();
        expect(notifications.length).toBe(0);
    });

    test("accepts `event` property as the event-type key", async () => {
        // pdfgen may use `event` instead of `type` — handler should match either.
        const { notifications } = await mountWidget();
        postMessageFrom("https://editor.pdfgeneratorapi.com", {
            event: "template:saved",
        });
        await animationFrame();
        expect(notifications.length).toBe(1);
        expect(notifications[0].message).toBe("Template saved.");
    });
});
