from .storage import invoice_by_key


def find_invoice(db, payment):
    """Match payment to invoice by (customer_id, invoice_number) identity only.

    Returns the invoice id if found, or None for an unmatched payment.
    An amount alone does not establish identity (BUSINESS_RULES.md).
    """
    exact = invoice_by_key(db, payment['customer_id'], payment['invoice_number'])
    return exact['id'] if exact else None
