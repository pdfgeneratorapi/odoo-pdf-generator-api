/** @odoo-module **/

import { describe, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { click, edit, press, queryAll, queryOne } from "@odoo/hoot-dom";

import {
    makeMockEnv,
    mockService,
    mountWithCleanup,
} from "@web/../tests/web_test_helpers";

import { PdfgenFieldPalette } from "@pdfgeneratorapi_connector/mapping_editor/field_palette";

const CANNED_FIELDS = {
    "account.move": {
        name: { string: "Reference", type: "char" },
        amount_total: { string: "Amount Total", type: "float" },
        partner_id: {
            string: "Customer",
            type: "many2one",
            relation: "res.partner",
        },
    },
    "res.partner": {
        name: { string: "Name", type: "char" },
        email: { string: "Email", type: "char" },
        country_id: {
            string: "Country",
            type: "many2one",
            relation: "res.country",
        },
    },
};

function mockLine(initial = {}) {
    const updateCalls = [];
    const line = {
        data: {
            odoo_field_path: initial.odoo_field_path || "",
            expression: initial.expression || "",
        },
        async update(vals) {
            updateCalls.push(vals);
            Object.assign(line.data, vals);
        },
    };
    line.updateCalls = updateCalls;
    return line;
}

function mockRecord(model, lines = []) {
    return {
        data: {
            model,
            line_ids: { records: lines },
        },
    };
}

async function mountPalette({ model = "account.move", lines = [] } = {}) {
    mockService("field", () => ({
        async loadFields(modelName) {
            return CANNED_FIELDS[modelName] || {};
        },
    }));
    await makeMockEnv();
    const record = mockRecord(model, lines);
    await mountWithCleanup(PdfgenFieldPalette, { props: { record } });
    await animationFrame();
    return { record };
}

describe("PdfgenFieldPalette", () => {
    test("loads fields from the record's model on mount", async () => {
        await mountPalette();
        const labels = queryAll(".o_pdfgen_palette_item .fw-semibold").map((el) =>
            el.textContent.trim()
        );
        expect(labels).toInclude("Reference");
        expect(labels).toInclude("Customer");
        expect(labels).toInclude("Amount Total");
        expect(queryOne(".breadcrumb-item.active").textContent.trim()).toBe(
            "account.move"
        );
    });

    test("filter narrows the visible list", async () => {
        await mountPalette();
        await edit("customer", { target: queryOne(".o_pdfgen_palette .form-control") });
        await animationFrame();
        const labels = queryAll(".o_pdfgen_palette_item .fw-semibold").map((el) =>
            el.textContent.trim().toLowerCase()
        );
        expect(labels.length).toBe(1);
        expect(labels[0]).toBe("customer");
    });

    test("clicking the relation chevron drills into the related model", async () => {
        await mountPalette();
        // Partner row's chevron — only relation fields render the button.
        const chevron = queryOne(".o_pdfgen_palette_item .btn-link");
        await click(chevron);
        await animationFrame();
        const crumbs = queryAll(".breadcrumb-item").map((el) =>
            el.textContent.trim()
        );
        expect(crumbs).toEqual(["account.move", "Customer"]);
        // New model's fields render.
        const labels = queryAll(".o_pdfgen_palette_item .fw-semibold").map((el) =>
            el.textContent.trim()
        );
        expect(labels).toInclude("Email");
    });

    test("breadcrumb link rewinds to the root", async () => {
        await mountPalette();
        await click(queryOne(".o_pdfgen_palette_item .btn-link"));
        await animationFrame();
        await click(queryOne(".breadcrumb-item:not(.active) a"));
        await animationFrame();
        expect(queryAll(".breadcrumb-item")).toHaveLength(1);
    });

    test("ArrowDown focuses the first item; Esc pops the breadcrumb", async () => {
        await mountPalette();
        queryOne(".o_pdfgen_palette").focus();
        await press("ArrowDown");
        await animationFrame();
        expect(queryOne(".o_pdfgen_palette_item_focused")).toBeDisplayed();
        // Drill in so we have something to pop out of.
        await click(queryOne(".o_pdfgen_palette_item .btn-link"));
        await animationFrame();
        expect(queryAll(".breadcrumb-item")).toHaveLength(2);
        queryOne(".o_pdfgen_palette").focus();
        await press("Escape");
        await animationFrame();
        expect(queryAll(".breadcrumb-item")).toHaveLength(1);
    });

    test("'/' from anywhere in the palette focuses the filter input", async () => {
        await mountPalette();
        queryOne(".o_pdfgen_palette").focus();
        await press("/");
        expect(document.activeElement).toBe(
            queryOne(".o_pdfgen_palette .form-control")
        );
    });
});
