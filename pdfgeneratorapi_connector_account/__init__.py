from . import models, wizards


def pre_init_hook(env):
    """Re-home invoice-dataset `ir.model.data` rows from the base addon.

    Before this bridge existed, the dataset for `account.move` (plus its
    ~30 mapping-line records) lived under the base `pdfgeneratorapi_connector`
    module's namespace. After the split, the same XML lives here under
    `pdfgeneratorapi_connector_account.*`. If the bridge installs into a DB
    that still has the records anchored to the old module, loading the data
    file would attempt to create a second `pdfgen.model.dataset` for
    `account.move` and trip its unique-per-model constraint.

    Solution: rewrite the `module` column of the affected rows so the data
    loader matches the existing records by external ID and UPDATEs them
    instead of inserting duplicates. Safe for fresh installs (the UPDATE
    matches zero rows).
    """
    env.cr.execute(
        """
        UPDATE ir_model_data
           SET module = 'pdfgeneratorapi_connector_account'
         WHERE module = 'pdfgeneratorapi_connector'
           AND model IN ('pdfgen.model.dataset', 'pdfgen.model.dataset.line')
           AND name LIKE 'dataset_%%'
        """
    )
