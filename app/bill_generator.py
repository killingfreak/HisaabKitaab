"""
bill_generator.py
Renders a Transaction as a printable PDF bill using reportlab (pure
Python — no system-level dependencies like WeasyPrint needs, so this
installs cleanly with just `pip install reportlab`).

Deliberately NOT GST-compliant yet: no sequential invoice numbering, no
HSN/SAC codes. That's a real formal requirement for filing, not just a
cosmetic one — revisit this module when that's needed rather than
bolting it on quietly.

NOTE ON `base_unit`: TransactionItem snapshots item_name/unit_price/tax_rate
at time of sale (see models.py), but NOT base_unit — it's fetched live
from Item here instead. That's a deliberate call: base_unit is a display
label, not a financial figure, so the small risk of it drifting if an
item's unit is ever renamed didn't seem to justify a schema migration
just for this. Revisit if that assumption stops holding.
"""

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import Party, Tenant, Transaction

_CURRENCY = "Rs."
# NOTE: the actual ₹ (U+20B9) glyph is NOT in reportlab's built-in Helvetica
# font (one of the 14 standard PDF base fonts, which predate this Unicode
# codepoint) — using it renders as a black "missing glyph" box, confirmed
# by actually rendering a sample bill to an image and looking at it. "Rs."
# is the zero-setup fix. To get the real ₹ symbol, embed a Unicode font
# (e.g. Noto Sans) via reportlab.pdfbase.ttfonts.TTFont + pdfmetrics.registerFont
# and reference it in every ParagraphStyle/TableStyle font name below.


def _fmt_money(value: Decimal) -> str:
    """Currency values are stored at NUMERIC(12,4) precision but always
    displayed to 2 decimal places on a bill."""
    return f"{_CURRENCY} {value:,.2f}"


def _fmt_qty(value: Decimal) -> str:
    """Quantities are stored at 4 decimal places but a bill should read
    '2.5 kg', not '2.5000 kg' — strip trailing zeros, keep at least one
    digit after the point only if it's non-integer."""
    normalized = value.normalize()
    # Decimal.normalize() can produce scientific-notation-like exponents
    # for whole numbers (e.g. 1E+1) — guard against that for display.
    text = f"{normalized:f}"
    return text


def build_bill_pdf(
    *,
    transaction: Transaction,
    tenant: Tenant,
    party: Party | None,
    unit_by_item_id: dict,
) -> io.BytesIO:
    """
    Build a one-page PDF bill for a single Transaction and return it as
    an in-memory buffer, positioned at the start (ready to stream out).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"{transaction.doc_type.value.title()} {transaction.id}",
    )

    styles = getSampleStyleSheet()
    business_style = ParagraphStyle(
        "Business", parent=styles["Title"], fontSize=18, spaceAfter=2
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=9, textColor=colors.grey
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Normal"],
        fontSize=10,
        spaceBefore=8,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )

    elements = []

    # --- Header: business identity + document type -------------------------
    elements.append(Paragraph(tenant.business_name, business_style))
    if tenant.gst_number:
        elements.append(Paragraph(f"GSTIN: {tenant.gst_number}", meta_style))
    elements.append(Spacer(1, 4 * mm))

    doc_title = (
        "TAX INVOICE" if transaction.doc_type.value == "INVOICE" else "DELIVERY CHALLAN"
    )
    elements.append(
        Paragraph(
            doc_title,
            ParagraphStyle("DocTitle", parent=styles["Heading2"], spaceAfter=2),
        )
    )
    elements.append(
        Paragraph(
            f"Bill #: {str(transaction.id)[:8].upper()} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Date: {transaction.created_at.strftime('%d %b %Y, %I:%M %p')}",
            meta_style,
        )
    )
    elements.append(Spacer(1, 4 * mm))

    # --- Bill To -------------------------------------------------------------
    elements.append(Paragraph("Bill To", section_style))
    if party is not None:
        bill_to_lines = [party.name]
        if party.phone:
            bill_to_lines.append(f"Phone: {party.phone}")
        if party.address:
            bill_to_lines.append(party.address)
    else:
        bill_to_lines = ["Walk-in Customer"]
    elements.append(Paragraph("<br/>".join(bill_to_lines), styles["Normal"]))
    elements.append(Spacer(1, 6 * mm))

    # --- Line items table ------------------------------------------------------
    header = ["Item", "Qty", "Unit", "Rate", "Tax %", "Amount"]
    rows = [header]
    for line in transaction.items:
        unit = unit_by_item_id.get(line.item_id, "")
        rows.append(
            [
                Paragraph(line.item_name, styles["Normal"]),
                _fmt_qty(line.quantity),
                unit,
                _fmt_money(line.unit_price),
                f"{line.tax_rate.normalize():f}%",
                _fmt_money(line.line_total),
            ]
        )

    items_table = Table(
        rows,
        colWidths=[62 * mm, 18 * mm, 16 * mm, 26 * mm, 18 * mm, 30 * mm],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d2d2d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f7f7f7")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(items_table)
    elements.append(Spacer(1, 6 * mm))

    # --- Totals ----------------------------------------------------------------
    totals_rows = [
        ["Subtotal", _fmt_money(transaction.subtotal)],
        ["Discount", f"- {_fmt_money(transaction.discount_amount)}"],
        ["Tax (GST)", _fmt_money(transaction.tax_amount)],
        ["Net Amount", _fmt_money(transaction.net_amount)],
        ["Paid", _fmt_money(transaction.paid_amount)],
        ["Balance Due", _fmt_money(transaction.balance_due)],
    ]
    totals_table = Table(totals_rows, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEABOVE", (0, 3), (-1, 3), 0.6, colors.black),  # above Net Amount
                ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
                ("FONTSIZE", (0, 3), (-1, 3), 10),
            ]
        )
    )
    elements.append(totals_table)
    elements.append(Spacer(1, 4 * mm))

    status_label = {
        "PAID": "PAID IN FULL",
        "PARTIAL": "PARTIALLY PAID",
        "UNPAID": "UNPAID — ON CREDIT",
    }[transaction.payment_status.value]
    elements.append(
        Paragraph(
            f"Payment status: <b>{status_label}</b>",
            ParagraphStyle("Status", parent=styles["Normal"], fontSize=10),
        )
    )

    elements.append(Spacer(1, 10 * mm))
    elements.append(
        Paragraph(
            "Thank you for your business!",
            ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=9,
                textColor=colors.grey,
                alignment=1,
            ),
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
